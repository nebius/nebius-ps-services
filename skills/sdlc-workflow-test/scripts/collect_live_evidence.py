#!/usr/bin/env python3
"""Collect one bounded private artifact for Agentic SDLC live verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

import verify_agentic_sdlc as verifier


SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy and hash one private live-verification artifact.",
    )
    parser.add_argument("--verification-root", type=Path, required=True)
    parser.add_argument(
        "--owner-kind",
        choices=("profile", "lane", "skill"),
        required=True,
    )
    parser.add_argument("--owner", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--name", required=True)
    return parser.parse_args(argv)


def owner_directory(kind: str, owner: str) -> Path:
    if kind == "profile" and owner in verifier.PROFILE_SOURCE_SCHEMAS:
        return Path("evidence") / "profiles" / owner / "sources"
    if kind == "lane" and owner in verifier.LIVE_LANES:
        return Path("evidence") / owner / "artifacts"
    if kind == "skill" and owner in verifier.REQUIRED_SDLC_SKILLS:
        return Path("evidence") / "skills" / owner / "artifacts"
    raise ValueError(f"unknown {kind} owner: {owner}")


def read_bounded_regular_file(source: Path) -> bytes:
    if source.is_symlink() or not source.is_file():
        raise ValueError("source must be a regular non-symlink file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(source, flags)
    try:
        source_stat = os.fstat(descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ValueError("source must be a regular non-symlink file")
        if not source_stat.st_size <= MAX_ARTIFACT_BYTES:
            raise ValueError("source exceeds the 10 MiB artifact limit")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(MAX_ARTIFACT_BYTES + 1)
        if len(content) > MAX_ARTIFACT_BYTES:
            raise ValueError("source exceeds the 10 MiB artifact limit")
        return content
    finally:
        os.close(descriptor)


def collect(
    verification_root: Path,
    *,
    owner_kind: str,
    owner: str,
    source: Path,
    name: str,
) -> dict[str, str]:
    requested_root = verification_root.expanduser()
    if requested_root.is_symlink():
        raise ValueError("verification root must be private and non-symlinked")
    root = requested_root.resolve(strict=True)
    marker = root / verifier.VERIFICATION_ROOT_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("verification root marker is missing or invalid")
    if os.name == "posix" and marker.stat().st_mode & 0o077:
        raise ValueError("verification root marker must be private")
    try:
        marker_value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("verification root marker is missing or invalid") from error
    if marker_value != {"schema": verifier.VERIFICATION_ROOT_MARKER_SCHEMA}:
        raise ValueError("verification root is not owned by sdlc-workflow-test")
    if root.is_symlink() or (os.name == "posix" and root.stat().st_mode & 0o077):
        raise ValueError("verification root must be private and non-symlinked")
    if SAFE_NAME.fullmatch(name) is None or name in {".", ".."}:
        raise ValueError("artifact name must be one safe filename")

    relative = owner_directory(owner_kind, owner) / name
    destination = root / relative
    if verifier.has_symlink_component(destination, root):
        raise ValueError("artifact destination contains a symlink")
    content = read_bounded_regular_file(source.expanduser())
    digest = hashlib.sha256(content).hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination.parent, 0o700)
    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise ValueError("artifact destination is not a regular file")
        destination_stat = destination.stat()
        if (
            destination_stat.st_size > MAX_ARTIFACT_BYTES
            or destination_stat.st_nlink != 1
        ):
            raise ValueError("artifact destination is unsafe")
        if destination.read_bytes() != content:
            raise ValueError("artifact destination already exists with different bytes")
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{name}.", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    os.chmod(destination, 0o600)
    return {"path": relative.as_posix(), "sha256": digest}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = collect(
            args.verification_root,
            owner_kind=args.owner_kind,
            owner=args.owner,
            source=args.source,
            name=args.name,
        )
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
