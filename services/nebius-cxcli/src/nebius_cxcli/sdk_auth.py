"""Shared Nebius SDK authentication helpers."""

from __future__ import annotations

import logging
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

_DELETED_KEY_REFRESH_LOG_MARKERS = (
    "public key not exists",
    "jwtkeynotexists",
    "expired or deactivated",
)


def _as_text(value: object) -> str:
    return str(value or "").strip()


def deleted_key_refresh_log(record: logging.LogRecord) -> bool:
    message = record.getMessage().lower()
    return "failed refresh token" in message and any(
        marker in message for marker in _DELETED_KEY_REFRESH_LOG_MARKERS
    )


class _SuppressDeletedKeyRefreshLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not deleted_key_refresh_log(record)


@contextmanager
def suppress_deleted_key_refresh_logs():
    """Suppress expected SDK token-refresh tracebacks for stale auth public keys."""

    refresh_logger = logging.getLogger("nebius.aio.token.renewable")
    deleted_key_filter = _SuppressDeletedKeyRefreshLog()
    refresh_logger.addFilter(deleted_key_filter)
    try:
        yield
    finally:
        refresh_logger.removeFilter(deleted_key_filter)


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
    prefer_operator_auth: bool = False,
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

    def _sdk_kwargs(**base: object) -> dict[str, object]:
        kwargs = dict(base)
        if endpoint_value:
            kwargs["endpoint"] = endpoint_value
        if parent_id:
            kwargs["parent_id"] = parent_id
        return kwargs

    def _sdk_from_credentials_file() -> object | None:
        credentials_file = _as_text(os.environ.get("NEBIUS_AUTH_CREDENTIALS_FILE"))
        if not credentials_file:
            return None
        path = Path(credentials_file).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"NEBIUS_AUTH_CREDENTIALS_FILE points to a missing file: {path}")
        return SDK(**_sdk_kwargs(credentials_file_name=path))

    def _sdk_from_service_account_env() -> object | None:
        service_account_id = _as_text(os.environ.get("NEBIUS_SA_ID"))
        auth_public_key_id = _as_text(os.environ.get("NEBIUS_AUTH_PUBLIC_KEY_ID"))
        private_key_file = _as_text(os.environ.get("NEBIUS_AUTH_PRIVATE_KEY_FILE"))
        if not (service_account_id and auth_public_key_id and private_key_file):
            return None
        key_path = Path(private_key_file).expanduser().resolve()
        if not key_path.exists() or not key_path.is_file():
            raise RuntimeError(f"NEBIUS_AUTH_PRIVATE_KEY_FILE points to a missing file: {key_path}")
        return SDK(
            **_sdk_kwargs(
                service_account_id=service_account_id,
                service_account_public_key_id=auth_public_key_id,
                service_account_private_key_file_name=key_path,
            )
        )

    def _sdk_from_iam_token() -> object | None:
        iam_token = _as_text(os.environ.get("NEBIUS_IAM_TOKEN")) or _ensure_iam_token_from_cli()
        if not iam_token:
            return None
        return SDK(**_sdk_kwargs(credentials=iam_token))

    def _sdk_from_config() -> object | None:
        try:
            from nebius.aio.cli_config import Config
        except Exception:
            return None

        config_kwargs: dict[str, object] = {}
        if profile:
            config_kwargs["profile"] = profile
        if endpoint_value:
            config_kwargs["endpoint"] = endpoint_value
        if config_file is not None:
            config_kwargs["config_file"] = config_file

        try:
            cfg = Config(**config_kwargs)
            return SDK(**_sdk_kwargs(config_reader=cfg))
        except Exception:
            return None

    auth_attempts = (
        (
            _sdk_from_iam_token,
            _sdk_from_config,
            _sdk_from_credentials_file,
            _sdk_from_service_account_env,
        )
        if prefer_operator_auth
        else (
            _sdk_from_credentials_file,
            _sdk_from_service_account_env,
            _sdk_from_iam_token,
            _sdk_from_config,
        )
    )
    for auth_attempt in auth_attempts:
        sdk = auth_attempt()
        if sdk is not None:
            return sdk

    raise RuntimeError(
        f"Failed to initialize Nebius SDK credentials for {context}. "
        "Provide NEBIUS_AUTH_CREDENTIALS_FILE, runtime auth env vars "
        "(NEBIUS_SA_ID/NEBIUS_AUTH_PUBLIC_KEY_ID/NEBIUS_AUTH_PRIVATE_KEY_FILE), "
        "set NEBIUS_IAM_TOKEN, log in with the Nebius CLI so "
        "`nebius iam get-access-token` works, or provide a Nebius SDK config/profile."
    )
