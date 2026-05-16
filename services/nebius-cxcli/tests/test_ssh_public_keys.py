from __future__ import annotations

import base64
from pathlib import Path

import pytest

from nebius_cxcli.ssh_public_keys import (
    discover_ssh_public_key_files,
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


def _ssh_wire_string(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def _valid_ecdsa_public_key() -> str:
    payload = (
        _ssh_wire_string(b"ecdsa-sha2-nistp256")
        + _ssh_wire_string(b"nistp256")
        + _ssh_wire_string(b"\x04" + (b"\x01" * 64))
    )
    blob = base64.b64encode(payload).decode("ascii")
    return f"ecdsa-sha2-nistp256 {blob} demo@example"


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


def test_normalize_ssh_public_key_value_accepts_inline_ecdsa() -> None:
    public_key = _valid_ecdsa_public_key()

    assert (
        normalize_ssh_public_key_value(
            public_key,
            field_label="infra.components[0].inputs.ssh_public_key",
        )
        == public_key
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


def test_discover_ssh_public_key_files_lists_supported_pub_files(tmp_path: Path) -> None:
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_rsa").write_text("not a public key\n", encoding="utf-8")
    (ssh_dir / "id_rsa.pub").write_text(_VALID_RSA_PUBLIC_KEY + "\n", encoding="utf-8")
    (ssh_dir / "my_ssh_key.pub").write_text(
        _valid_ecdsa_public_key() + "\n", encoding="utf-8"
    )
    (ssh_dir / "broken.pub").write_text("ssh-ed25519 not-base64\n", encoding="utf-8")
    (ssh_dir / "binary.pub").write_bytes(b"\xff\xfe\x00")

    discovered = discover_ssh_public_key_files(ssh_dir=ssh_dir)

    assert [item.path.name for item in discovered] == ["id_rsa.pub", "my_ssh_key.pub"]
    assert discovered[0].public_key == _VALID_RSA_PUBLIC_KEY
    assert discovered[1].key_type == "ecdsa-sha2-nistp256"


def test_normalize_ssh_public_key_value_rejects_unsupported_key_type() -> None:
    with pytest.raises(ValueError, match="supported key"):
        normalize_ssh_public_key_value(
            "ssh-dss AAAAB3NzaC1kc3MAAACBAKc= demo@example",
            field_label="infra.components[0].inputs.ssh_public_key",
        )


def test_normalize_ssh_public_key_value_rejects_malformed_inline_key() -> None:
    with pytest.raises(ValueError, match="supported key"):
        normalize_ssh_public_key_value(
            "ssh-ed25519 AAAA/not-a-real-key demo@example",
            field_label="infra.components[0].inputs.ssh_public_key",
        )
