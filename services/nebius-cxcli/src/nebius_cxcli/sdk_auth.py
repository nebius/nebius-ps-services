"""Shared Nebius SDK authentication helpers."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from types import MethodType
from typing import Any

_DELETED_KEY_REFRESH_LOG_MARKERS = (
    "public key not exists",
    "jwtkeynotexists",
    "expired or deactivated",
)
_RETRYABLE_REFRESH_LOG_MARKERS = (
    "deadline_exceeded",
    "deadline exceeded",
)
_RETRYABLE_REQUEST_LOG_MARKERS = (
    "request attempt",
    "but will be retried",
)
_LOGGER = logging.getLogger(__name__)


class _ManagedSdkEventLoop:
    """Dedicated loop for Nebius SDK synchronous wait helpers."""

    def __init__(self, *, context: str) -> None:
        self.loop = asyncio.new_event_loop()
        self._context = context
        self._lock = threading.Lock()
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False

    def ensure_started(self) -> None:
        if self.loop.is_running():
            return
        with self._lock:
            if self._closed:
                raise RuntimeError(f"Nebius SDK event loop is closed for {self._context}.")
            if self.loop.is_running():
                return
            self._thread = threading.Thread(
                target=self._run,
                name=f"nebius-cxcli-sdk-loop-{self._context}",
                daemon=True,
            )
            self._thread.start()
        if not self._started.wait(timeout=5):
            raise RuntimeError(f"Timed out starting Nebius SDK event loop for {self._context}.")

    def stop(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            if self.loop.is_closed():
                self._stopped.set()
                return
            if self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)
            else:
                self.loop.close()
                self._stopped.set()
                return
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._started.set()
        try:
            self.loop.run_forever()
        finally:
            pending = tuple(asyncio.all_tasks(self.loop))
            if pending:
                for task in pending:
                    task.cancel()
                self.loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            asyncio.set_event_loop(None)
            self.loop.close()
            self._stopped.set()


def _attach_managed_sdk_event_loop(sdk: Any, manager: _ManagedSdkEventLoop) -> Any:
    original_run_sync = sdk.run_sync
    original_close = sdk.close

    def _run_sync(_self: object, awaitable: Any, timeout: float | None = None) -> Any:
        manager.ensure_started()
        return original_run_sync(awaitable, timeout)

    async def _close(_self: object, grace: float | None = None) -> None:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        try:
            if running_loop is manager.loop:
                await original_close(grace)
            else:
                manager.ensure_started()
                future = asyncio.run_coroutine_threadsafe(
                    original_close(grace),
                    manager.loop,
                )
                await asyncio.wrap_future(future)
        finally:
            manager.stop()

    def _sync_close(_self: object, timeout: float | None = None) -> None:
        try:
            manager.ensure_started()
            return original_run_sync(original_close(None), timeout)
        finally:
            manager.stop()

    sdk.run_sync = MethodType(_run_sync, sdk)
    sdk.close = MethodType(_close, sdk)
    sdk.sync_close = MethodType(_sync_close, sdk)
    sdk._cxcli_sdk_event_loop_manager = manager
    return sdk


def _as_text(value: object) -> str:
    return str(value or "").strip()


def deleted_key_refresh_log(record: logging.LogRecord) -> bool:
    message = record.getMessage().lower()
    return "failed refresh token" in message and any(
        marker in message for marker in _DELETED_KEY_REFRESH_LOG_MARKERS
    )


def retryable_refresh_log(record: logging.LogRecord) -> bool:
    message = record.getMessage().lower()
    return "failed refresh token" in message and any(
        marker in message for marker in _RETRYABLE_REFRESH_LOG_MARKERS
    )


def retryable_request_log(record: logging.LogRecord) -> bool:
    message = record.getMessage().lower()
    return all(marker in message for marker in _RETRYABLE_REQUEST_LOG_MARKERS)


class _SuppressDeletedKeyRefreshLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not deleted_key_refresh_log(record)


class _SuppressExpectedRefreshLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not (deleted_key_refresh_log(record) or retryable_refresh_log(record))


class _SuppressExpectedRequestRetryLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not retryable_request_log(record)


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


@contextmanager
def suppress_expected_refresh_logs():
    """Suppress SDK token-refresh tracebacks that cxcli already retries or wraps."""

    refresh_logger = logging.getLogger("nebius.aio.token.renewable")
    expected_filter = _SuppressExpectedRefreshLog()
    refresh_logger.addFilter(expected_filter)
    try:
        yield
    finally:
        refresh_logger.removeFilter(expected_filter)


@contextmanager
def suppress_expected_sdk_retry_logs():
    """Suppress SDK retry tracebacks for requests that are still being retried."""

    request_logger = logging.getLogger("nebius.aio.request")
    request_filter = _SuppressExpectedRequestRetryLog()
    request_logger.addFilter(request_filter)
    try:
        yield
    finally:
        request_logger.removeFilter(request_filter)


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
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    token = cp.stdout.strip()
    if token:
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
    allow_cli_token: bool = True,
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

    def _create_sdk(**base: object) -> object:
        manager = _ManagedSdkEventLoop(context=context)
        try:
            sdk = SDK(**_sdk_kwargs(event_loop=manager.loop, **base))
        except Exception:
            manager.stop()
            raise
        return _attach_managed_sdk_event_loop(sdk, manager)

    last_auth_error: Exception | None = None

    def _sdk_from_credentials_file() -> object | None:
        credentials_file = _as_text(os.environ.get("NEBIUS_AUTH_CREDENTIALS_FILE"))
        if not credentials_file:
            return None
        path = Path(credentials_file).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"NEBIUS_AUTH_CREDENTIALS_FILE points to a missing file: {path}")
        return _create_sdk(credentials_file_name=path)

    def _sdk_from_service_account_env() -> object | None:
        service_account_id = _as_text(os.environ.get("NEBIUS_SA_ID"))
        auth_public_key_id = _as_text(os.environ.get("NEBIUS_AUTH_PUBLIC_KEY_ID"))
        private_key_file = _as_text(os.environ.get("NEBIUS_AUTH_PRIVATE_KEY_FILE"))
        if not (service_account_id and auth_public_key_id and private_key_file):
            return None
        key_path = Path(private_key_file).expanduser().resolve()
        if not key_path.exists() or not key_path.is_file():
            raise RuntimeError(f"NEBIUS_AUTH_PRIVATE_KEY_FILE points to a missing file: {key_path}")
        return _create_sdk(
            service_account_id=service_account_id,
            service_account_public_key_id=auth_public_key_id,
            service_account_private_key_file_name=key_path,
        )

    def _sdk_from_iam_token_env() -> object | None:
        iam_token = _as_text(os.environ.get("NEBIUS_IAM_TOKEN"))
        if not iam_token:
            return None
        return _create_sdk(credentials=iam_token)

    def _sdk_from_cli_token() -> object | None:
        iam_token = _ensure_iam_token_from_cli()
        if not iam_token:
            return None
        return _create_sdk(credentials=iam_token)

    def _sdk_from_config() -> object | None:
        nonlocal last_auth_error
        try:
            from nebius.aio.cli_config import Config
        except Exception as exc:
            _LOGGER.debug(
                "Nebius SDK config reader is unavailable for %s: %s",
                context,
                exc,
                exc_info=True,
            )
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
            return _create_sdk(config_reader=cfg)
        except Exception as exc:
            last_auth_error = exc
            _LOGGER.debug(
                "Nebius SDK config auth attempt failed for %s: %s",
                context,
                exc,
                exc_info=True,
            )
            return None

    def _has_codex_agent_credentials_file() -> bool:
        credentials_file = _as_text(os.environ.get("NEBIUS_AUTH_CREDENTIALS_FILE"))
        profile_name = _as_text(os.environ.get("NEBIUS_PROFILE"))
        if not credentials_file or not profile_name.startswith("codex-agent-"):
            return False
        project_id = profile_name.removeprefix("codex-agent-")
        path = Path(credentials_file).expanduser()
        return (
            path.name == f"codex-agent-authkey.{project_id}.json"
            and path.exists()
            and path.is_file()
        )

    if prefer_operator_auth:
        auth_attempts = []
        if _has_codex_agent_credentials_file():
            auth_attempts.append(_sdk_from_credentials_file)
        auth_attempts.extend([_sdk_from_iam_token_env, _sdk_from_config])
        if allow_cli_token:
            auth_attempts.append(_sdk_from_cli_token)
        if not _has_codex_agent_credentials_file():
            auth_attempts.append(_sdk_from_credentials_file)
        auth_attempts.append(_sdk_from_service_account_env)
    else:
        auth_attempts = [
            _sdk_from_credentials_file,
            _sdk_from_service_account_env,
            _sdk_from_iam_token_env,
            _sdk_from_config,
        ]
        if allow_cli_token:
            auth_attempts.append(_sdk_from_cli_token)
    for auth_attempt in auth_attempts:
        sdk = auth_attempt()
        if sdk is not None:
            return sdk

    cli_hint = (
        "log in with the Nebius CLI so `nebius iam get-access-token` works, or "
        if allow_cli_token
        else ""
    )
    error = RuntimeError(
        f"Failed to initialize Nebius SDK credentials for {context}. "
        "Provide NEBIUS_AUTH_CREDENTIALS_FILE, runtime auth env vars "
        "(NEBIUS_SA_ID/NEBIUS_AUTH_PUBLIC_KEY_ID/NEBIUS_AUTH_PRIVATE_KEY_FILE), "
        f"set NEBIUS_IAM_TOKEN, {cli_hint}provide a Nebius SDK config/profile."
    )
    if last_auth_error is not None:
        raise error from last_auth_error
    raise error
