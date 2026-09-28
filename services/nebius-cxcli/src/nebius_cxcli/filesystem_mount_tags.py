"""Shared filesystem attachment tags that fit the virtio-fs device field."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

# The Compute API admits 37 characters, but virtio_fs_config.tag is 36 bytes.
# QEMU rejects longer UTF-8 tags before realizing the filesystem device.
MOUNT_TAG_MAX_BYTES = 36


def mount_tag_error(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return "Mount tag must not be empty."
    if len(value.encode("utf-8")) > MOUNT_TAG_MAX_BYTES:
        return f"Mount tag must be at most {MOUNT_TAG_MAX_BYTES} UTF-8 bytes."
    return None


def default_mount_tag(value: str) -> str:
    """Bound generated defaults while keeping distinct role/target identities."""
    encoded = value.encode("utf-8")
    if len(encoded) <= MOUNT_TAG_MAX_BYTES:
        return value
    suffix = hashlib.sha256(encoded).hexdigest()[:10]
    # Discard only a trailing partial code point at the byte boundary.
    prefix = encoded[: MOUNT_TAG_MAX_BYTES - len(suffix) - 1].decode("utf-8", errors="ignore")
    return f"{prefix}-{suffix}"


def validate_filesystem_mount_tags(inputs: Mapping[str, Any], *, label: str) -> None:
    filesystems = inputs.get("filesystems")
    if isinstance(filesystems, Mapping) and filesystems:
        entries = [(f"{label}.filesystems.{key}", spec) for key, spec in filesystems.items()]
    else:
        entries = [(label, inputs)]
    for path, spec in entries:
        if not isinstance(spec, Mapping) or spec.get("mount_tag") is None:
            continue
        if error := mount_tag_error(spec["mount_tag"]):
            raise ValueError(f"{path}.mount_tag: {error}")
