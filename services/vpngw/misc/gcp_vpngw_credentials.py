#!/usr/bin/env python3
"""Private, per-tunnel credentials for the ordinary GCP VPN helper.

Only file locations leave this process. gcloud receives secrets through an
inherited flags-file descriptor, never through argv or inherited environment.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


def private_directory(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError("Credential directory must be absolute")
    missing = []
    parent = path
    while not parent.exists():
        if parent.is_symlink():
            raise ValueError("Unsafe credential directory")
        missing.append(parent)
        parent = parent.parent
    for item in (parent, *parent.parents):
        metadata = item.lstat()
        sticky_root = metadata.st_uid == 0 and bool(metadata.st_mode & stat.S_ISVTX)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid not in {0, os.getuid()}
            or (metadata.st_mode & 0o022 and not sticky_root)
        ):
            raise ValueError("Unsafe credential directory")
    for item in reversed(missing):
        item.mkdir(mode=0o700)
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise ValueError("Credential directory must be private and operator owned")


def read(path: Path) -> dict | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "r") as source:
        metadata = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise ValueError("Unsafe credential file")
        content = source.read(65537)

        def signature(item):
            return (
                item.st_dev,
                item.st_ino,
                item.st_uid,
                item.st_mode,
                item.st_size,
                item.st_mtime_ns,
                item.st_ctime_ns,
            )

        if signature(os.fstat(source.fileno())) != signature(metadata) or signature(
            path.lstat()
        ) != signature(metadata):
            raise ValueError("Credential file changed while being read")
    if len(content) > 65536:
        raise ValueError("Credential file is too large")
    return json.loads(content)


def publish(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".credentials-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        if path.is_symlink():
            raise ValueError("Unsafe credential destination")
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_secret(secret: object) -> None:
    """Validate provider syntax and the product size bound without echoing inputs."""
    if not isinstance(secret, str) or not re.fullmatch(r"[ -~]{1,4096}", secret):
        raise ValueError("Missing or invalid retained tunnel credential")


def prepare(
    previous: dict | None,
    names: tuple[str, str],
    exists: tuple[bool, bool],
    rotate: bool,
    supplied: tuple[str, str],
) -> dict:
    if previous is not None and (
        previous.get("schema") != 1 or set(previous.get("tunnels", {})) != set(names)
    ):
        raise ValueError("Stored credential target differs from this connection")
    if previous and previous["pending"]:
        for name, value in zip(names, supplied, strict=True):
            if value and previous["tunnels"][name] != value:
                raise ValueError("Pending credentials differ; retry the same keys")
        for name, present in zip(names, exists, strict=True):
            if rotate or not present:
                validate_secret(previous["tunnels"][name])
        return previous
    values = {}
    for name, present, value in zip(names, exists, supplied, strict=True):
        retained = previous["tunnels"][name] if previous else None
        if present and not rotate:
            # GCP does not disclose an existing PSK. An override is not evidence
            # that this unchanged remote tunnel uses it.
            values[name] = retained
        else:
            values[name] = value or (retained if not rotate else None) or secrets.token_urlsafe(48)
            validate_secret(values[name])
    return {"schema": 1, "pending": True, "tunnels": values}


def create(arguments: list[str], secret: str) -> None:
    validate_secret(secret)
    # An unlinked private descriptor avoids pipe-buffer deadlocks for escaped
    # multibyte values. No pathname containing credentials is exposed to gcloud.
    with tempfile.TemporaryFile(mode="w+b") as flags:
        flags.write(json.dumps({"--shared-secret": secret}, ensure_ascii=False).encode())
        flags.flush()
        flags.seek(0)
        environment = {
            name: value
            for name, value in os.environ.items()
            if name not in {"PSK1", "PSK2"} and value != secret
        }
        try:
            result = subprocess.run(
                ["gcloud", *arguments, f"--flags-file=/dev/fd/{flags.fileno()}"],
                pass_fds=(flags.fileno(),),
                env=environment,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            if result.returncode:
                raise RuntimeError("Tunnel creation failed; credentials retained for retry")
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                "Tunnel creation timed out; credentials retained for retry"
            ) from None


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "create", "render"))
    parser.add_argument("project")
    parser.add_argument("region")
    parser.add_argument("connection")
    parser.add_argument("values", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    for name in (args.project, args.region, args.connection):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", name):
            raise ValueError("Invalid credential target identifier")
    root = (
        Path.home()
        / ".local"
        / "state"
        / "nebius-vpngw"
        / "gcp"
        / args.project
        / args.region
        / args.connection
    )
    private_directory(root)
    lock_fd = os.open(root / "credentials.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise ValueError("Unsafe credential lock")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = root / "credentials.json"
        record = read(path)
        if args.action == "prepare":
            first, second, exists1, exists2, rotate = args.values
            record = prepare(
                record,
                (first, second),
                (exists1 == "true", exists2 == "true"),
                rotate == "1",
                (os.environ.get("PSK1", ""), os.environ.get("PSK2", "")),
            )
            publish(path, json.dumps(record, sort_keys=True) + "\n")
            print(f"Private tunnel credentials: {path}")
        elif args.action == "create":
            if record is None or not record["pending"]:
                raise ValueError("Prepare credentials before creating a tunnel")
            name, *arguments = args.values
            create(arguments, record["tunnels"][name])
        else:
            import yaml

            if record is None:
                raise ValueError("Credential state is missing")
            text = sys.stdin.read(65537)
            if len(text) > 65536:
                raise ValueError("Connection document is too large")
            document = yaml.safe_load(text)
            connection = document["connections"][0]
            if connection["name"] != args.connection:
                raise ValueError("Connection document target differs")
            if {tunnel["name"] for tunnel in connection["tunnels"]} != set(record["tunnels"]):
                raise ValueError("Connection document tunnels differ")
            for tunnel in connection["tunnels"]:
                value = record["tunnels"][tunnel["name"]]
                if value is not None:
                    tunnel["psk"] = value
            output = root / "connection.yaml"
            publish(output, yaml.safe_dump(document, sort_keys=False))
            record["pending"] = False
            publish(path, json.dumps(record, sort_keys=True) + "\n")
            print(f"Private Nebius connection configuration: {output}")
            if any(value is None for value in record["tunnels"].values()):
                print("Existing unknown tunnel PSKs remain environment placeholders in that file.")
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    try:
        run()
    except Exception:
        # Parser, provider, and YAML errors can contain secret inputs.
        print(
            "Private credential operation failed; keep retained files and retry the same connection.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
