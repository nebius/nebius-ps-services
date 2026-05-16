"""SSH public key normalization helpers."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SUPPORTED_SSH_KEY_TYPES = (
    "ssh-rsa",
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
)
_INLINE_SSH_PUBLIC_KEY_RE = re.compile(
    r"^((?:ssh-(?:rsa|ed25519))|(?:ecdsa-sha2-nistp(?:256|384|521)))\s+([A-Za-z0-9+/]+={0,3})(?:\s+(.+))?$"
)
_ECDSA_CURVE_KEY_LENGTHS = {
    "nistp256": 65,
    "nistp384": 97,
    "nistp521": 133,
}


@dataclass(frozen=True)
class SSHPublicKeyFile:
    path: Path
    display_path: str
    public_key: str
    key_type: str
    comment: str


def _looks_like_path(value: str) -> bool:
    token = value.strip()
    if token.startswith("ssh-"):
        return False
    return token.startswith(("~", ".", "/")) or "/" in token or "\\" in token


def _inline_ssh_public_key(value: str) -> str | None:
    token = str(value).strip()
    if not token:
        return ""
    match = _INLINE_SSH_PUBLIC_KEY_RE.fullmatch(token)
    if match is None:
        return None
    key_type, key_blob, comment = match.groups()
    padded_blob = key_blob + ("=" * (-len(key_blob) % 4))
    try:
        decoded = base64.b64decode(padded_blob.encode("ascii"), validate=True)
    except (ValueError, binascii.Error):
        return None
    if not decoded or not _matches_ssh_public_key_wire_format(decoded, key_type):
        return None
    normalized = f"{key_type} {key_blob}"
    if comment:
        normalized = f"{normalized} {comment.strip()}"
    return normalized


def _read_ssh_wire_field(payload: bytes, offset: int) -> tuple[bytes, int] | None:
    if offset + 4 > len(payload):
        return None
    size = int.from_bytes(payload[offset : offset + 4], "big")
    offset += 4
    if size < 0 or offset + size > len(payload):
        return None
    return payload[offset : offset + size], offset + size


def _matches_ssh_public_key_wire_format(payload: bytes, key_type: str) -> bool:
    header = _read_ssh_wire_field(payload, 0)
    if header is None:
        return False
    algorithm, offset = header
    if algorithm.decode("ascii", errors="ignore") != key_type:
        return False
    if key_type == "ssh-ed25519":
        public_key = _read_ssh_wire_field(payload, offset)
        if public_key is None:
            return False
        key_bytes, offset = public_key
        return len(key_bytes) == 32 and offset == len(payload)
    if key_type == "ssh-rsa":
        exponent = _read_ssh_wire_field(payload, offset)
        if exponent is None:
            return False
        exponent_bytes, offset = exponent
        modulus = _read_ssh_wire_field(payload, offset)
        if modulus is None:
            return False
        modulus_bytes, offset = modulus
        return bool(exponent_bytes) and bool(modulus_bytes) and offset == len(payload)
    if key_type.startswith("ecdsa-sha2-"):
        expected_curve = key_type.removeprefix("ecdsa-sha2-")
        curve = _read_ssh_wire_field(payload, offset)
        if curve is None:
            return False
        curve_bytes, offset = curve
        curve_name = curve_bytes.decode("ascii", errors="ignore")
        if curve_name != expected_curve:
            return False
        public_key = _read_ssh_wire_field(payload, offset)
        if public_key is None:
            return False
        key_bytes, offset = public_key
        expected_len = _ECDSA_CURVE_KEY_LENGTHS.get(curve_name)
        return (
            expected_len is not None
            and len(key_bytes) == expected_len
            and key_bytes.startswith(b"\x04")
            and offset == len(payload)
        )
    return False


def _existing_key_path(raw_value: str, *, base_dir: Path | None = None) -> Path | None:
    token = str(raw_value).strip()
    if not token:
        return None
    expanded = Path(token).expanduser()
    candidates: list[Path] = []
    if expanded.is_absolute():
        candidates.append(expanded)
    else:
        if base_dir is not None:
            candidates.append((base_dir / expanded).expanduser())
        candidates.append(expanded)
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def normalize_ssh_public_key_value(
    value: Any,
    *,
    field_label: str,
    base_dir: Path | None = None,
) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_label} must be a string")

    token = value.strip()
    if not token:
        return ""

    inline = _inline_ssh_public_key(token)
    if inline is not None:
        return inline

    key_path = _existing_key_path(token, base_dir=base_dir)
    if key_path is None:
        if _looks_like_path(token):
            raise ValueError(f"{field_label} file not found: {token}")
        allowed = ", ".join(_SUPPORTED_SSH_KEY_TYPES)
        raise ValueError(
            f"{field_label} must be an inline SSH public key or a readable local file path "
            f"containing one supported key ({allowed})"
        )

    try:
        text = key_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{field_label} could not be read from {key_path}: {exc}") from exc

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError(f"{field_label} file must contain exactly one SSH public key line")

    inline = _inline_ssh_public_key(lines[0])
    if inline is None:
        allowed = ", ".join(_SUPPORTED_SSH_KEY_TYPES)
        raise ValueError(
            f"{field_label} file must contain one supported SSH public key ({allowed})"
        )
    return inline


def _display_path(path: Path) -> str:
    expanded = path.expanduser()
    home = Path.home().expanduser()
    with suppress(ValueError):
        relative = expanded.relative_to(home)
        return f"~/{relative.as_posix()}"
    return str(expanded)


def _key_file_sort_key(candidate: SSHPublicKeyFile) -> tuple[int, str]:
    preferred = {
        "id_ed25519.pub": 0,
        "id_ecdsa.pub": 1,
        "id_rsa.pub": 2,
    }
    return (preferred.get(candidate.path.name, 10), candidate.display_path)


def _public_key_parts(public_key: str) -> tuple[str, str]:
    parts = public_key.split(None, 2)
    key_type = parts[0] if parts else ""
    comment = parts[2].strip() if len(parts) > 2 else ""
    return key_type, comment


def discover_ssh_public_key_files(
    *,
    ssh_dir: Path | None = None,
) -> tuple[SSHPublicKeyFile, ...]:
    """Return readable supported public keys from one local .ssh directory."""
    directory = (ssh_dir if ssh_dir is not None else Path.home() / ".ssh").expanduser()
    if not directory.is_dir():
        return ()

    candidates: list[SSHPublicKeyFile] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError:
        return ()
    for path in entries:
        if not path.is_file() or path.suffix != ".pub":
            continue
        try:
            public_key = normalize_ssh_public_key_value(
                str(path),
                field_label=f"SSH public key file {path}",
            )
        except ValueError:
            continue
        key_type, comment = _public_key_parts(public_key)
        candidates.append(
            SSHPublicKeyFile(
                path=path,
                display_path=_display_path(path),
                public_key=public_key,
                key_type=key_type,
                comment=comment,
            )
        )
    return tuple(sorted(candidates, key=_key_file_sort_key))


def normalize_runtime_ssh_public_key_inputs(
    payload: Mapping[str, Any] | dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    infra = payload.get("infra")
    if not isinstance(infra, dict):
        return False
    components = infra.get("components")
    if not isinstance(components, list):
        return False

    changed = False
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            continue
        inputs = component.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if "ssh_public_key" not in inputs:
            continue
        original = inputs.get("ssh_public_key")
        normalized = normalize_ssh_public_key_value(
            original,
            field_label=f"infra.components[{index}].inputs.ssh_public_key",
            base_dir=base_dir,
        )
        if normalized != original:
            inputs["ssh_public_key"] = normalized
            changed = True
    return changed


__all__ = [
    "SSHPublicKeyFile",
    "discover_ssh_public_key_files",
    "normalize_runtime_ssh_public_key_inputs",
    "normalize_ssh_public_key_value",
]
