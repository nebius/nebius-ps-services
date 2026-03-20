"""Helpers for Nebius Terraform provider metadata values."""

from __future__ import annotations

import hashlib
import re

DEFAULT_PROVIDER_MODULE_NAME = "nebius_cxcli"
PROVIDER_MODULE_NAME_MAX_LENGTH = 16
PROVIDER_MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,16}$")


def _normalize_provider_module_segment(value: str | None) -> str:
    token = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "").strip())
    token = re.sub(r"_+", "_", token).strip("_")
    return token


def build_provider_module_name(*, client_name: str | None, project_id: str | None) -> str:
    """Build a deterministic Nebius provider `module_name` value.

    Nebius provider accepts only `[A-Za-z0-9_]` and a maximum length of 16.
    Keep a short human hint from the client name and add a digest for stability.
    """

    normalized_client = _normalize_provider_module_segment(client_name)[:7].strip("_")
    digest_source = "|".join(
        token
        for token in (
            _normalize_provider_module_segment(client_name),
            _normalize_provider_module_segment(project_id),
        )
        if token
    )
    if not digest_source:
        return DEFAULT_PROVIDER_MODULE_NAME

    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:4]
    if normalized_client:
        candidate = f"ncx_{normalized_client}_{digest}"
    else:
        candidate = f"ncx_{hashlib.sha1(digest_source.encode('utf-8')).hexdigest()[:12]}"
    if PROVIDER_MODULE_NAME_PATTERN.fullmatch(candidate):
        return candidate
    return DEFAULT_PROVIDER_MODULE_NAME


def is_valid_provider_module_name(value: str | None) -> bool:
    return bool(PROVIDER_MODULE_NAME_PATTERN.fullmatch(str(value or "").strip()))


__all__ = [
    "DEFAULT_PROVIDER_MODULE_NAME",
    "PROVIDER_MODULE_NAME_MAX_LENGTH",
    "PROVIDER_MODULE_NAME_PATTERN",
    "build_provider_module_name",
    "is_valid_provider_module_name",
]
