"""Runtime-only Nsight login input; credentials never enter project state."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import re
import subprocess
import sys
from contextlib import nullcontext, suppress

import typer

from .nsight_runtime import NsightKubernetes, validate_login_secret
from .soperator_upgrade_progress import SoperatorUpgradeProgress

MAX_PASSWORD_BYTES = 4096


def _validate_password(password):
    if (
        not isinstance(password, str)
        or not password.strip()
        or len(password.encode()) > MAX_PASSWORD_BYTES
        or any(c in password for c in ("\n", "\r", "\0"))
    ):
        raise ValueError("Nsight password must be nonblank, single-line and at most 4096 bytes")


def _read_secret(kube, name):
    try:
        output = kube.run(
            ["-n", "soperator", "get", "secret", name, "--ignore-not-found=true", "-o", "json"]
        )
        value = json.loads(output) if output.strip() else None
        if value is not None and (
            not isinstance(value, dict)
            or value.get("metadata", {}).get("name") != name
            or value.get("metadata", {}).get("namespace") != "soperator"
            or not value.get("metadata", {}).get("uid")
        ):
            raise ValueError("Unexpected identity")
        return value
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
        raise RuntimeError("Cannot read the selected Nsight login Secret; check access.") from None


def _matches(secret, username, password):
    try:
        data = secret["data"]
        return hmac.compare_digest(
            base64.b64decode(data["username"], validate=True), username.encode()
        ) and hmac.compare_digest(
            base64.b64decode(data["password"], validate=True), password.encode()
        )
    except (KeyError, TypeError, ValueError, binascii.Error):
        return False


def prepare_login_credentials(
    *,
    env,
    name,
    username,
    interactive,
    password_stdin,
    fence,
    progress: SoperatorUpgradeProgress | None = None,
):
    """Create only by explicit input; equal retries reuse, unequal retries fail."""
    if interactive and password_stdin:
        raise ValueError("Choose --interactive or --password-stdin, not both")
    if not (interactive or password_stdin):
        return
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", username):
        raise ValueError("Nsight username requires 1-128 letters, digits, _, ., @ or -")
    kube = NsightKubernetes(env)
    with (
        progress.phase("nsight-login-read", "Check the Nsight browser login Secret")
        if progress is not None
        else nullcontext()
    ):
        fence()
        existing = _read_secret(kube, name)
        if interactive and existing is not None:
            validate_login_secret(
                kube,
                {
                    "namespace": "soperator",
                    "values": {
                        key: {"secretName": name, "secretKey": field}
                        for key, field in (("webUsername", "username"), ("webPassword", "password"))
                    },
                },
            )
            return
    # Input owns the terminal; no live progress may run while reading credentials.
    if password_stdin:
        if sys.stdin.isatty():
            raise ValueError(
                "--password-stdin requires piped input; use --interactive at a terminal"
            )
        try:
            password = sys.stdin.read(MAX_PASSWORD_BYTES + 2)
        except (OSError, UnicodeError):
            raise ValueError("Cannot read the Nsight password from stdin") from None
        if password.endswith("\n"):
            password = password[:-1].removesuffix("\r")
        _validate_password(password)
    else:
        if not sys.stdin.isatty():
            raise ValueError(
                "The Nsight login wizard requires a terminal to create a missing Secret; "
                "run in a terminal or use --password-stdin in automation. "
                "Use --no-interactive to require an existing login Secret."
            )
        username = typer.prompt("Nsight browser username", default=username)
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", username):
            raise ValueError("Invalid Nsight browser username")
        while True:
            password = typer.prompt(
                "Nsight browser password", hide_input=True, confirmation_prompt=True
            )
            try:
                _validate_password(password)
            except ValueError as exc:
                typer.echo(str(exc), err=True)
            else:
                break
    if existing is not None:
        if not _matches(existing, username, password):
            raise RuntimeError(
                "The existing Nsight login Secret differs; credentials were not changed"
            )
        return
    payload = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {"name": name, "namespace": "soperator"},
        "stringData": {"username": username, "password": password},
    }
    with (
        progress.phase("nsight-login-create", "Create and verify the Nsight browser login Secret")
        if progress is not None
        else nullcontext()
    ):
        fence()
        # Resolve a lost response or an external creator without overwriting it.
        with suppress(RuntimeError, OSError, subprocess.SubprocessError):
            kube.run(
                ["-n", "soperator", "create", "-f", "-", "-o", "name"],
                input_text=json.dumps(payload),
            )
        observed = _read_secret(kube, name)
        if observed is None or not _matches(observed, username, password):
            raise RuntimeError(
                "Nsight login Secret creation could not be verified; no credentials were overwritten"
            )
        fence()
