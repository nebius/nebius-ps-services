#!/usr/bin/env python3
"""Execute arbitrary children with just-in-time Nebius bearer credentials."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

from nebius_auth_shared import CredentialError, credential_identity_from_file


PROFILE_PREFIX = "codex-agent-"
CREDENTIAL_PREFIX = "codex-agent-authkey."
CREDENTIAL_SUFFIX = ".json"
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
IAM_ENV = "_".join(("NEBIUS", "IAM", "TOKEN"))
GENERIC_ENV = "".join(("TO", "KEN"))
TOKEN_TIMEOUT_SECONDS = 30


class RuntimeContextError(ValueError):
    """Injected runtime context is absent, inconsistent, or unsafe."""


def _runtime_context() -> tuple[str, str, Path]:
    profile = os.environ.get("NEBIUS_PROFILE", "").strip()
    project_id = os.environ.get("NEBIUS_PROJECT_ID", "").strip()
    credential_text = os.environ.get("NEBIUS_AUTH_CREDENTIALS_FILE", "").strip()
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise RuntimeContextError("NEBIUS_PROJECT_ID is missing or invalid")
    if profile != f"{PROFILE_PREFIX}{project_id}":
        raise RuntimeContextError("NEBIUS_PROFILE does not match the selected project")
    if not credential_text:
        raise RuntimeContextError("NEBIUS_AUTH_CREDENTIALS_FILE is missing")

    credential = Path(os.path.abspath(os.path.expanduser(credential_text)))
    expected = Path.home() / ".nebius" / (
        f"{CREDENTIAL_PREFIX}{project_id}{CREDENTIAL_SUFFIX}"
    )
    if credential != expected:
        raise RuntimeContextError("credential path is not canonical for the project")
    try:
        credential_identity_from_file(credential, expected_uid=os.getuid())
    except CredentialError as exc:
        raise RuntimeContextError(f"credential validation failed: {exc}") from exc
    return profile, project_id, credential


def _base_child_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop(GENERIC_ENV, None)
    environment.pop(IAM_ENV, None)
    return environment


def _mint_access_credential(profile: str) -> str:
    environment = _base_child_environment()
    try:
        result = subprocess.run(
            [
                "nebius",
                "iam",
                "get-access-token",
                "--no-browser",
                "--profile",
                profile,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=TOKEN_TIMEOUT_SECONDS,
            env=environment,
        )
    except OSError as exc:
        raise RuntimeContextError(
            "cannot execute the Nebius CLI for non-interactive credential mint"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeContextError(
            "non-interactive credential mint exceeded its timeout"
        ) from exc
    credential = result.stdout.strip()
    if (
        result.returncode != 0
        or not credential
        or any(character.isspace() for character in credential)
    ):
        raise RuntimeContextError(
            "non-interactive credential mint failed or returned no credential"
        )
    return credential


def _bearer_environment(profile: str) -> dict[str, str]:
    environment = _base_child_environment()
    environment[IAM_ENV] = _mint_access_credential(profile)
    return environment


def _normalized_status(returncode: int) -> int:
    return 128 + (-returncode) if returncode < 0 else returncode


def _run_once(command: list[str], *, profile: str) -> int:
    try:
        result = subprocess.run(command, check=False, env=_bearer_environment(profile))
    except OSError as exc:
        print(f"ERROR: cannot execute credential-scoped child: {exc}", file=sys.stderr)
        return 126
    return _normalized_status(result.returncode)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a child with a fresh Nebius bearer credential without printing "
            "it or placing it in argv."
        )
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for name in ("exec-token", "retry-idempotent"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error(f"{args.operation} requires -- <command> [args...]")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        profile, _, _ = _runtime_context()
        if args.operation == "exec-token":
            try:
                os.execvpe(
                    args.command[0], args.command, _bearer_environment(profile)
                )
            except OSError as exc:
                print(
                    f"ERROR: cannot execute credential-scoped child: {exc}",
                    file=sys.stderr,
                )
                return 126
            raise AssertionError("os.execvpe returned unexpectedly")

        status = _run_once(args.command, profile=profile)
        if status != 77:
            return status
        return _run_once(args.command, profile=profile)
    except RuntimeContextError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
