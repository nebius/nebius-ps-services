"""SSH public key normalization helpers."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_SUPPORTED_SSH_KEY_TYPES = ("ssh-rsa", "ssh-ed25519")
_INLINE_SSH_PUBLIC_KEY_RE = re.compile(
    r"^(ssh-(?:rsa|ed25519))\s+([A-Za-z0-9+/]+={0,3})(?:\s+(.+))?$"
)


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
    except OSError as exc:
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
    "normalize_runtime_ssh_public_key_inputs",
    "normalize_ssh_public_key_value",
]
