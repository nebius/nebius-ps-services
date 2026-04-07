"""Shared Nebius SDK authentication helpers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _as_text(value: object) -> str:
    return str(value or "").strip()


def _ensure_iam_token_from_cli(*, timeout_seconds: int = 10) -> str | None:
    token = _as_text(os.environ.get("NEBIUS_IAM_TOKEN"))
    if token:
        return token
    try:
        cp = subprocess.run(
            ["nebius", "iam", "get-access-token", "--format", "text"],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except Exception:
        return None
    token = cp.stdout.strip()
    if token:
        os.environ["NEBIUS_IAM_TOKEN"] = token
        return token
    return None


def init_nebius_sdk(
    *,
    profile: str | None = None,
    endpoint: str | None = None,
    config_file: Path | None = None,
    parent_id: str | None = None,
    context: str = "Nebius API",
):
    """Initialize Nebius SDK with robust auth fallback order."""
    try:
        from nebius.sdk import SDK
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            f"Nebius SDK is required for {context}. "
            'Install dependencies with `pip install -e ".[dev]"`.'
        ) from exc

    endpoint_value = _as_text(endpoint) or _as_text(os.environ.get("NEBIUS_ENDPOINT")) or None

    credentials_file = _as_text(os.environ.get("NEBIUS_AUTH_CREDENTIALS_FILE"))
    if credentials_file:
        path = Path(credentials_file).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"NEBIUS_AUTH_CREDENTIALS_FILE points to a missing file: {path}")
        kwargs: dict[str, object] = {"credentials_file_name": path}
        if endpoint_value:
            kwargs["endpoint"] = endpoint_value
        if parent_id:
            kwargs["parent_id"] = parent_id
        return SDK(**kwargs)

    service_account_id = _as_text(os.environ.get("NEBIUS_SA_ID"))
    auth_public_key_id = _as_text(os.environ.get("NEBIUS_AUTH_PUBLIC_KEY_ID"))
    private_key_file = _as_text(os.environ.get("NEBIUS_AUTH_PRIVATE_KEY_FILE"))
    if service_account_id and auth_public_key_id and private_key_file:
        key_path = Path(private_key_file).expanduser().resolve()
        if not key_path.exists() or not key_path.is_file():
            raise RuntimeError(f"NEBIUS_AUTH_PRIVATE_KEY_FILE points to a missing file: {key_path}")
        kwargs = {
            "service_account_id": service_account_id,
            "service_account_public_key_id": auth_public_key_id,
            "service_account_private_key_file_name": key_path,
        }
        if endpoint_value:
            kwargs["endpoint"] = endpoint_value
        if parent_id:
            kwargs["parent_id"] = parent_id
        return SDK(**kwargs)

    iam_token = _as_text(os.environ.get("NEBIUS_IAM_TOKEN")) or _ensure_iam_token_from_cli()
    if iam_token:
        kwargs = {"credentials": iam_token}
        if endpoint_value:
            kwargs["endpoint"] = endpoint_value
        if parent_id:
            kwargs["parent_id"] = parent_id
        return SDK(**kwargs)

    try:
        from nebius.aio.cli_config import Config
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            f"Failed to initialize Nebius SDK credentials for {context}. "
            "Provide NEBIUS_AUTH_CREDENTIALS_FILE, service-account key env values, "
            "set NEBIUS_IAM_TOKEN, log in with the Nebius CLI so "
            "`nebius iam get-access-token` works, or provide a Nebius SDK config/profile."
        ) from exc

    config_kwargs: dict[str, object] = {}
    if profile:
        config_kwargs["profile"] = profile
    if endpoint_value:
        config_kwargs["endpoint"] = endpoint_value
    if config_file is not None:
        config_kwargs["config_file"] = config_file

    try:
        cfg = Config(**config_kwargs)
        kwargs = {"config_reader": cfg}
        if parent_id:
            kwargs["parent_id"] = parent_id
        return SDK(**kwargs)
    except Exception as exc:  # pragma: no cover - runtime integration
        raise RuntimeError(
            f"Failed to initialize Nebius SDK credentials for {context}. "
            "Provide NEBIUS_AUTH_CREDENTIALS_FILE, runtime auth env vars "
            "(NEBIUS_SA_ID/NEBIUS_AUTH_PUBLIC_KEY_ID/NEBIUS_AUTH_PRIVATE_KEY_FILE), "
            "set NEBIUS_IAM_TOKEN, log in with the Nebius CLI so "
            "`nebius iam get-access-token` works, or provide a Nebius SDK config/profile."
        ) from exc
