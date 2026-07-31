#!/usr/bin/env python3
"""Secure local file operations for Nebius agent-auth repair leases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from typing import Any


HOOK_DIR = Path(__file__).resolve().parent.parent / "assets" / "hooks"
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from nebius_auth_shared import (  # noqa: E402
    CredentialError,
    credential_service_account_id as shared_credential_service_account_id,
)


PURPOSE = "codex-agent-local-auth-repair"
ALLOWED_ACTIONS = ["chmod-credential-0600", "rebuild-profile"]
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
LEASE_NAME_RE = re.compile(r"repair-lease\.([0-9a-f]{32})\.json")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
MAX_LEASE_BYTES = 64 * 1024
MAX_CREDENTIAL_BYTES = 1024 * 1024


def fail(message: str) -> None:
    raise SystemExit(message)


def open_owned_regular(path: Path, expected_uid: int) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        fail("this platform cannot enforce no-follow file access")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_NOFOLLOW
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        fail(f"cannot open {path} safely: {exc}")
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != expected_uid:
        os.close(descriptor)
        fail(f"{path} is not an owned regular file")
    return descriptor


def read_descriptor(descriptor: int, max_bytes: int) -> bytes:
    parts: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 131072)
        if not chunk:
            return b"".join(parts)
        total += len(chunk)
        if total > max_bytes:
            fail(f"file exceeds the {max_bytes}-byte safety limit")
        parts.append(chunk)


def credential_service_account_id(raw: bytes) -> str:
    try:
        return shared_credential_service_account_id(raw)
    except CredentialError as exc:
        fail(f"bound credential identity is invalid: {exc}")


def require_directory(path: Path, expected_uid: int, mode: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        fail(f"cannot inspect {path}: {exc}")
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != expected_uid:
        fail(f"{path} is not an owned non-symlink directory")
    if stat.S_IMODE(metadata.st_mode) != mode:
        fail(f"{path} must have mode {mode:04o}")


def canonical_digest(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def credential_sha256(args: argparse.Namespace) -> None:
    descriptor = open_owned_regular(Path(args.path), args.uid)
    try:
        print(
            hashlib.sha256(
                read_descriptor(descriptor, MAX_CREDENTIAL_BYTES)
            ).hexdigest()
        )
    finally:
        os.close(descriptor)


def secure_chmod(args: argparse.Namespace) -> None:
    descriptor = open_owned_regular(Path(args.path), args.uid)
    try:
        raw = read_descriptor(descriptor, MAX_CREDENTIAL_BYTES)
        if args.expected_sha256 and hashlib.sha256(raw).hexdigest() != args.expected_sha256:
            fail("credential changed before permission repair")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def issue(args: argparse.Namespace) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        fail("this platform cannot enforce no-follow file creation")
    for value in (args.project_id, args.tenant_id, args.service_account_id):
        if not IDENTIFIER_RE.fullmatch(value):
            fail("cannot issue repair lease with an invalid identity")
    if not NAME_RE.fullmatch(args.service_account_name):
        fail("cannot issue repair lease with an invalid service-account name")
    if args.profile != f"codex-agent-{args.project_id}":
        fail("cannot issue repair lease for a mismatched profile")
    if not SHA256_RE.fullmatch(args.credential_sha256) or not SHA256_RE.fullmatch(
        args.plan_digest
    ):
        fail("cannot issue repair lease with an invalid digest")
    if args.expires_at <= args.issued_at:
        fail("cannot issue repair lease with an invalid lifetime")
    lease_dir = Path(args.lease_dir)
    expected_credential = lease_dir.parent / f"codex-agent-authkey.{args.project_id}.json"
    if Path(args.credential_file) != expected_credential:
        fail("cannot issue repair lease for a noncanonical credential path")
    require_directory(lease_dir.parent, args.uid, 0o700)
    require_directory(lease_dir, args.uid, 0o700)
    nonce = secrets.token_hex(16)
    payload: dict[str, Any] = {
        "allowed_actions": ALLOWED_ACTIONS,
        "authorization_plan_digest": args.plan_digest,
        "credential_file": args.credential_file,
        "credential_sha256": args.credential_sha256,
        "endpoint": args.endpoint,
        "expires_at_epoch": args.expires_at,
        "issued_at_epoch": args.issued_at,
        "nonce": nonce,
        "profile": args.profile,
        "project_id": args.project_id,
        "purpose": PURPOSE,
        "schema_version": 1,
        "service_account_id": args.service_account_id,
        "service_account_name": args.service_account_name,
        "tenant_id": args.tenant_id,
    }
    payload["integrity_sha256"] = canonical_digest(payload)
    path = lease_dir / f"repair-lease.{nonce}.json"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_NOFOLLOW
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, indent=2)
            stream.write("\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    print(path)


def validate(args: argparse.Namespace) -> None:
    lease_path = Path(args.lease_file)
    home = Path(args.home)
    nebius_dir = home / ".nebius"
    lease_dir = nebius_dir / "codex-agent-repair-leases"
    require_directory(nebius_dir, args.uid, 0o700)
    require_directory(lease_dir, args.uid, 0o700)
    name_match = LEASE_NAME_RE.fullmatch(lease_path.name)
    if lease_path.parent != lease_dir or name_match is None:
        fail("invalid repair lease: path is outside the canonical repair-lease directory")

    descriptor = open_owned_regular(lease_path, args.uid)
    try:
        metadata = os.fstat(descriptor)
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            fail("invalid repair lease: lease must have mode 0600")
        raw_lease = read_descriptor(descriptor, MAX_LEASE_BYTES)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw_lease)
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail(f"invalid repair lease: cannot parse lease: {exc}")

    expected_keys = {
        "allowed_actions",
        "authorization_plan_digest",
        "credential_file",
        "credential_sha256",
        "endpoint",
        "expires_at_epoch",
        "integrity_sha256",
        "issued_at_epoch",
        "nonce",
        "profile",
        "project_id",
        "purpose",
        "schema_version",
        "service_account_id",
        "service_account_name",
        "tenant_id",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        fail("invalid repair lease: schema fields do not match version 1")
    integrity = value.pop("integrity_sha256")
    if not isinstance(integrity, str) or canonical_digest(value) != integrity:
        fail("invalid repair lease: integrity digest does not match")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["purpose"] != PURPOSE
    ):
        fail("invalid repair lease: schema version or purpose is not supported")
    if value["allowed_actions"] != ALLOWED_ACTIONS:
        fail("invalid repair lease: allowed actions are not the fixed local-repair allowlist")
    if value["endpoint"] != args.endpoint:
        fail("invalid repair lease: endpoint does not match")

    for key in ("project_id", "tenant_id", "service_account_id"):
        if not isinstance(value[key], str) or not IDENTIFIER_RE.fullmatch(value[key]):
            fail(f"invalid repair lease: {key} is invalid")
    if not isinstance(value["service_account_name"], str) or not NAME_RE.fullmatch(
        value["service_account_name"]
    ):
        fail("invalid repair lease: service_account_name is invalid")
    if (
        not isinstance(value["profile"], str)
        or value["profile"] != f"codex-agent-{value['project_id']}"
    ):
        fail("invalid repair lease: profile is not bound to the selected project")
    expected_credential = nebius_dir / f"codex-agent-authkey.{value['project_id']}.json"
    if not isinstance(value["credential_file"], str) or Path(
        value["credential_file"]
    ) != expected_credential:
        fail("invalid repair lease: credential path is not canonical")
    for key in ("credential_sha256", "authorization_plan_digest"):
        if not isinstance(value[key], str) or not SHA256_RE.fullmatch(value[key]):
            fail(f"invalid repair lease: {key} is invalid")
    if not isinstance(value["nonce"], str) or value["nonce"] != name_match.group(1):
        fail("invalid repair lease: nonce does not match the filename")

    issued_at = value["issued_at_epoch"]
    expires_at = value["expires_at_epoch"]
    now = int(time.time())
    if type(issued_at) is not int or type(expires_at) is not int:
        fail("invalid repair lease: timestamps must be integers")
    if issued_at > now or expires_at <= now:
        fail("invalid repair lease: lease is not currently valid")
    if expires_at <= issued_at or expires_at - issued_at > args.max_ttl:
        fail("invalid repair lease: lease lifetime exceeds the allowed bound")

    credential_descriptor = open_owned_regular(expected_credential, args.uid)
    try:
        raw_credential = read_descriptor(
            credential_descriptor, MAX_CREDENTIAL_BYTES
        )
    finally:
        os.close(credential_descriptor)
    if hashlib.sha256(raw_credential).hexdigest() != value["credential_sha256"]:
        fail("invalid repair lease: bound credential content has changed")
    if credential_service_account_id(raw_credential) != value["service_account_id"]:
        fail("invalid repair lease: bound credential identity has changed")
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    fingerprint = subparsers.add_parser("credential-sha256")
    fingerprint.add_argument("--path", required=True)
    fingerprint.add_argument("--uid", required=True, type=int)
    fingerprint.set_defaults(handler=credential_sha256)

    chmod_parser = subparsers.add_parser("secure-chmod")
    chmod_parser.add_argument("--path", required=True)
    chmod_parser.add_argument("--uid", required=True, type=int)
    chmod_parser.add_argument("--expected-sha256", default="")
    chmod_parser.set_defaults(handler=secure_chmod)

    issue_parser = subparsers.add_parser("issue")
    for option in (
        "lease-dir",
        "project-id",
        "tenant-id",
        "service-account-id",
        "service-account-name",
        "profile",
        "credential-file",
        "credential-sha256",
        "endpoint",
        "plan-digest",
    ):
        issue_parser.add_argument(f"--{option}", required=True)
    issue_parser.add_argument("--issued-at", required=True, type=int)
    issue_parser.add_argument("--expires-at", required=True, type=int)
    issue_parser.add_argument("--uid", required=True, type=int)
    issue_parser.set_defaults(handler=issue)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--lease-file", required=True)
    validate_parser.add_argument("--home", required=True)
    validate_parser.add_argument("--uid", required=True, type=int)
    validate_parser.add_argument("--max-ttl", required=True, type=int)
    validate_parser.add_argument("--endpoint", required=True)
    validate_parser.set_defaults(handler=validate)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
