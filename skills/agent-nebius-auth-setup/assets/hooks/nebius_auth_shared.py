#!/usr/bin/env python3
"""Shared, fail-closed parsing for Codex Agent Nebius credentials."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


MAX_CREDENTIAL_FILE_BYTES = 1024 * 1024
SERVICE_ACCOUNT_ID_RE = re.compile(
    r"^serviceaccount-[A-Za-z0-9][A-Za-z0-9._:-]*$"
)
LEGACY_ID_FIELDS = (
    "service_account_id",
    "serviceAccountId",
    "account_id",
    "accountId",
)


class CredentialError(ValueError):
    """Credential content or local-file safety validation failed."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CredentialError(f"credential contains duplicate JSON key: {key}")
        result[key] = value
    return result


def _contains_legacy_identity(value: Any) -> bool:
    if isinstance(value, dict):
        if any(field in value for field in LEGACY_ID_FIELDS):
            return True
        if "service_account" in value:
            return True
        return any(_contains_legacy_identity(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_legacy_identity(item) for item in value)
    return False


def credential_service_account_id(raw: bytes) -> str:
    """Return the service-account ID from current Nebius CLI credential JSON."""

    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except CredentialError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CredentialError(f"credential is malformed JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CredentialError("credential must be a JSON object")

    if _contains_legacy_identity(value):
        raise CredentialError("legacy service-account identity fields are unsupported")

    subject_credentials = value.get("subject-credentials")
    if not isinstance(subject_credentials, dict):
        raise CredentialError("credential is missing subject-credentials")
    if subject_credentials.get("type") != "JWT":
        raise CredentialError("credential type must be JWT")
    if subject_credentials.get("alg") != "RS256":
        raise CredentialError("credential algorithm must be RS256")
    issuer = subject_credentials.get("iss")
    subject_id = subject_credentials.get("sub")
    if not isinstance(issuer, str) or not isinstance(subject_id, str):
        raise CredentialError("credential issuer and subject must be strings")
    if issuer != subject_id:
        raise CredentialError("credential issuer and subject identities conflict")
    if not SERVICE_ACCOUNT_ID_RE.fullmatch(issuer):
        raise CredentialError("credential subject is not a valid service-account ID")
    return issuer


def read_owned_credential(
    path: Path,
    *,
    expected_uid: int,
    require_mode_0600: bool = True,
) -> tuple[bytes, os.stat_result]:
    """Read an owned regular credential through one no-follow descriptor."""

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CredentialError(f"cannot open credential safely: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CredentialError("credential must be a regular file")
        if metadata.st_uid != expected_uid:
            raise CredentialError("credential is not owned by the current user")
        if require_mode_0600 and stat.S_IMODE(metadata.st_mode) != 0o600:
            raise CredentialError("credential must have mode 0600")
        if metadata.st_size > MAX_CREDENTIAL_FILE_BYTES:
            raise CredentialError("credential exceeds the size limit")

        parts: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 131072)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_CREDENTIAL_FILE_BYTES:
                raise CredentialError("credential exceeds the size limit")
            parts.append(chunk)
        return b"".join(parts), metadata
    finally:
        os.close(descriptor)


def credential_identity_from_file(
    path: Path,
    *,
    expected_uid: int,
    require_mode_0600: bool = True,
) -> str:
    raw, _ = read_owned_credential(
        path,
        expected_uid=expected_uid,
        require_mode_0600=require_mode_0600,
    )
    return credential_service_account_id(raw)


def credential_file_safety_issue(path: Path, *, expected_uid: int) -> str:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unsafe"
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        return "unsafe"
    if metadata.st_uid != expected_uid:
        return "unsafe"
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        return "mode"
    try:
        credential_identity_from_file(path, expected_uid=expected_uid)
    except CredentialError:
        return "unsafe"
    return ""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a Nebius service-account credential without exposing it."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    identity = subparsers.add_parser("credential-id")
    identity.add_argument("--path", type=Path, required=True)
    identity.add_argument("--uid", type=int, required=True)
    identity.add_argument("--allow-non-0600", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        identity = credential_identity_from_file(
            args.path,
            expected_uid=args.uid,
            require_mode_0600=not args.allow_non_0600,
        )
    except CredentialError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(identity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
