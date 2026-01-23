from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from pathlib import Path

from nebius.aio.cli_config import Config as NebiusConfig

from .errors import ConfigError


def ensure_access_token(
    *,
    required: bool = True,
    timeout_seconds: int = 60,
    logger: logging.Logger | None = None,
    config_file: Path | None = None,
    profile: str | None = None,
) -> str | None:
    """Ensure an IAM token is available for SDK calls.

    Resolution order:
    1) Existing NEBIUS_IAM_TOKEN in the environment.
    2) Token from Nebius CLI config (fast path).
    3) Token via Nebius CLI `iam get-access-token`.

    Returns the token string if resolved, otherwise None (when required=False).
    """
    log = logger or logging.getLogger("nebius-acc")

    token = os.environ.get("NEBIUS_IAM_TOKEN")
    if token:
        return token

    token = _get_token_from_cli_config(log, config_file=config_file, profile=profile)
    if token:
        os.environ["NEBIUS_IAM_TOKEN"] = token
        log.info("Using IAM token from Nebius CLI config.")
        return token

    token = _get_token_from_cli(
        timeout_seconds=timeout_seconds, log=log, config_file=config_file, profile=profile
    )
    if token:
        os.environ["NEBIUS_IAM_TOKEN"] = token
        log.info("Fetched IAM token using Nebius CLI.")
        return token

    if required:
        raise ConfigError(
            "Unable to obtain Nebius IAM token. Set NEBIUS_IAM_TOKEN or configure "
            "Nebius CLI credentials so 'nebius iam get-access-token' works."
        )
    log.warning("No IAM token found; SDK will rely on configured credentials.")
    return None


def _get_token_from_cli_config(
    log: logging.Logger, *, config_file: Path | None, profile: str | None
) -> str | None:
    try:
        kwargs: dict[str, object] = {"no_parent_id": True}
        if config_file:
            kwargs["config_file"] = config_file
        if profile:
            kwargs["profile"] = profile
        cfg = NebiusConfig(**kwargs)
    except Exception as exc:
        log.debug("Failed to read Nebius CLI config: %s", exc)
        return None

    for attr in ("token", "access_token"):
        try:
            value = getattr(cfg, attr)
            if callable(value):
                value = value()
            if isinstance(value, str) and value:
                return value
        except Exception:
            continue

    try:
        getter = getattr(cfg, "get", None)
        if callable(getter):
            value = getter("token")
            if isinstance(value, str) and value:
                return value
    except Exception:
        return None
    return None


def _get_token_from_cli(
    *, timeout_seconds: int, log: logging.Logger, config_file: Path | None, profile: str | None
) -> str | None:
    def _run(cmd: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            shlex.split(cmd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

    config_flags = []
    if config_file:
        config_flags.extend(["--config", str(config_file)])
    if profile:
        config_flags.extend(["--profile", profile])
    config_suffix = " " + " ".join(config_flags) if config_flags else ""

    try:
        res = _run(f"nebius iam get-access-token --format json{config_suffix}")
        if res.returncode == 0:
            token = _parse_token_output(res.stdout)
            if token:
                return token
        res = _run(f"nebius iam get-access-token{config_suffix}")
        if res.returncode == 0:
            token = res.stdout.strip()
            if token:
                return token
    except FileNotFoundError:
        log.debug("Nebius CLI not found in PATH.")
    except subprocess.TimeoutExpired:
        log.warning("Timed out while requesting IAM token via Nebius CLI.")
    except Exception as exc:
        log.debug("Unexpected error while requesting IAM token: %s", exc)
    return None


def _parse_token_output(stdout: str) -> str | None:
    raw = stdout.strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(payload, dict):
        token = payload.get("access_token") or payload.get("token")
        if isinstance(token, str) and token:
            return token
    return None
