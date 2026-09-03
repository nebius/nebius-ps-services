from __future__ import annotations

import asyncio
import math
import os
import threading
import typing as t

from nebius.aio.token.token import Bearer, Receiver, Token

from .vpngw_sa import _cli_access_token

_CLI_TOKEN_BUDGET_SECONDS = 30.0
_SDK_USER_AGENT_PREFIX = "nebius-vpngw"


class NebiusCLIAuthenticationError(RuntimeError):
    """A redacted failure to obtain credentials from the current CLI profile."""


def error_chain_has_cli_authentication_failure(error: BaseException) -> bool:
    """Match only explicit causes owned by the bounded CLI credential source."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, NebiusCLIAuthenticationError):
            return True
        current = current.__cause__
    return False


class _NebiusCLIAccessTokenReceiver(Receiver):
    def __init__(self, parent: NebiusCLIAccessTokenCredentials) -> None:
        super().__init__()
        self._parent = parent
        self._attempt = 0
        self._latest = None

    async def _fetch(
        self,
        timeout: float | None = None,
        options: dict[str, str] | None = None,
    ) -> Token:
        del options
        self._attempt += 1
        stale_token = self._latest if self._attempt > 1 else None
        return await self._parent.fetch_token(
            timeout=timeout,
            force_refresh=self._attempt > 1,
            stale_token=stale_token,
        )

    def can_retry(
        self,
        err: Exception,
        options: dict[str, str] | None = None,
    ) -> bool:
        del err, options
        return self._attempt < 2


class NebiusCLIAccessTokenCredentials(Bearer):
    """SDK bearer backed by bounded current-profile CLI token acquisition."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._cached_token: Token | None = None

    @property
    def name(self) -> str:
        return "nebius-cli-current-profile"

    def receiver(self) -> Receiver:
        return _NebiusCLIAccessTokenReceiver(self)

    async def fetch_token(
        self,
        *,
        timeout: float | None,
        force_refresh: bool,
        stale_token: Token | None,
    ) -> Token:
        budget = _CLI_TOKEN_BUDGET_SECONDS
        if timeout is not None and math.isfinite(timeout) and timeout > 0:
            budget = min(timeout, budget)
        budget = max(0.1, budget)

        return await asyncio.to_thread(
            self._fetch_token_locked,
            budget=budget,
            force_refresh=force_refresh,
            stale_token=stale_token,
        )

    def _fetch_token_locked(
        self,
        *,
        budget: float,
        force_refresh: bool,
        stale_token: Token | None,
    ) -> Token:
        with self._lock:
            if not force_refresh and self._cached_token is not None:
                return self._cached_token
            if (
                force_refresh
                and self._cached_token is not None
                and stale_token is not None
                and self._cached_token is not stale_token
            ):
                return self._cached_token

            cli_timeout = max(1, math.floor(max(1.0, budget - 1.0)))
            token = _cli_access_token(
                timeout_seconds=cli_timeout,
                ignore_ambient_token=True,
                no_browser=True,
                process_timeout_seconds=budget,
            )
            if token is None:
                raise NebiusCLIAuthenticationError(
                    "Nebius CLI could not provide a current access token"
                )
            self._cached_token = Token(token)
            return self._cached_token


def renewable_cli_access_token_credentials() -> Bearer:
    """Return the one canonical tokenless-apply credential source."""

    return NebiusCLIAccessTokenCredentials()


def build_operator_sdk_client(*, explicit_token: str | None) -> t.Any:
    """Build an operator SDK without making explicit tokens depend on CLI config.

    An explicit endpoint is authoritative. An explicitly selected profile is
    consulted only when its endpoint is needed. Tokenless operation keeps the
    renewable CLI bearer and default-profile endpoint discovery.
    """

    from nebius.sdk import SDK  # type: ignore

    endpoint = os.environ.get("NEBIUS_ENDPOINT") or None
    profile = os.environ.get("NEBIUS_PROFILE") or None
    credentials: str | Bearer
    if explicit_token is not None:
        credentials = explicit_token
    else:
        credentials = renewable_cli_access_token_credentials()

    if endpoint is not None:
        return SDK(
            credentials=credentials,
            domain=endpoint,
            user_agent_prefix=_SDK_USER_AGENT_PREFIX,
        )

    if profile is not None or explicit_token is None:
        from nebius.aio.cli_config import Config  # type: ignore

        config_reader = Config(
            no_env=True,
            no_parent_id=True,
            profile=profile,
            endpoint=None,
        )
        return SDK(
            credentials=credentials,
            config_reader=config_reader,
            user_agent_prefix=_SDK_USER_AGENT_PREFIX,
        )

    return SDK(
        credentials=credentials,
        user_agent_prefix=_SDK_USER_AGENT_PREFIX,
    )
