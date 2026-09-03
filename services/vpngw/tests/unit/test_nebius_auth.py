from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from nebius.aio.cli_config import Config
from nebius.aio.token.token import Token
from nebius.sdk import SDK

from nebius_vpngw.nebius_auth import (
    NebiusCLIAccessTokenCredentials,
    NebiusCLIAuthenticationError,
    build_operator_sdk_client,
)


def test_cli_bearer_caches_then_forces_one_request_refresh() -> None:
    async def scenario() -> None:
        credentials = NebiusCLIAccessTokenCredentials()
        receiver = credentials.receiver()
        first = await receiver.fetch(timeout=5.0)
        assert first.token == "token-a"
        assert receiver.can_retry(RuntimeError("rejected"))
        second = await receiver.fetch(timeout=5.0)
        assert second.token == "token-b"
        assert not receiver.can_retry(RuntimeError("rejected again"))

        another_receiver = credentials.receiver()
        shared = await another_receiver.fetch(timeout=5.0)
        assert shared is second

    with patch(
        "nebius_vpngw.nebius_auth._cli_access_token",
        side_effect=("token-a", "token-b"),
    ) as token_source:
        asyncio.run(scenario())

    assert token_source.call_count == 2
    for token_call in token_source.call_args_list:
        assert token_call.kwargs["ignore_ambient_token"] is True
        assert token_call.kwargs["no_browser"] is True
        assert 0 < token_call.kwargs["process_timeout_seconds"] <= 30


def test_cli_bearer_failure_is_redacted_and_bounded() -> None:
    async def scenario() -> None:
        receiver = NebiusCLIAccessTokenCredentials().receiver()
        with pytest.raises(NebiusCLIAuthenticationError) as first:
            await receiver.fetch(timeout=0.5)
        assert str(first.value) == "Nebius CLI could not provide a current access token"
        assert receiver.can_retry(first.value)

        with pytest.raises(NebiusCLIAuthenticationError) as second:
            await receiver.fetch(timeout=0.5)
        assert str(second.value) == str(first.value)
        assert not receiver.can_retry(second.value)

    with patch(
        "nebius_vpngw.nebius_auth._cli_access_token",
        return_value=None,
    ) as token_source:
        asyncio.run(scenario())

    assert token_source.call_count == 2


def test_cli_bearer_is_shared_safely_across_event_loops() -> None:
    credentials = NebiusCLIAccessTokenCredentials()
    ready = threading.Barrier(2)

    def fetch_from_thread() -> Token:
        ready.wait(timeout=2)
        return asyncio.run(credentials.receiver().fetch(timeout=5.0))

    with (
        patch(
            "nebius_vpngw.nebius_auth._cli_access_token",
            return_value="shared-token",
        ) as token_source,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        first = executor.submit(fetch_from_thread)
        second = executor.submit(fetch_from_thread)
        tokens = (first.result(timeout=5), second.result(timeout=5))

    assert tokens[0] is tokens[1]
    assert tokens[0].token == "shared-token"
    token_source.assert_called_once()


def test_operator_sdk_explicit_token_does_not_require_cli_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NEBIUS_PROFILE", raising=False)
    monkeypatch.delenv("NEBIUS_ENDPOINT", raising=False)

    sdk = build_operator_sdk_client(explicit_token="explicit-token")
    try:
        assert sdk.get_authorization_provider() is not None
    finally:
        sdk.sync_close()


def test_operator_sdk_explicit_endpoint_does_not_require_cli_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEBIUS_PROFILE", "missing-profile")
    monkeypatch.setenv("NEBIUS_ENDPOINT", "api.eu.nebius.cloud:443")

    with (
        patch("nebius.sdk.SDK") as sdk,
        patch("nebius.aio.cli_config.Config") as config,
    ):
        client = build_operator_sdk_client(explicit_token="explicit-token")

    assert client is sdk.return_value
    config.assert_not_called()
    sdk.assert_called_once_with(
        credentials="explicit-token",
        domain="api.eu.nebius.cloud:443",
        user_agent_prefix="nebius-vpngw",
    )


def test_operator_sdk_selected_profile_supplies_endpoint_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEBIUS_PROFILE", "operator-profile")
    monkeypatch.delenv("NEBIUS_ENDPOINT", raising=False)

    with (
        patch("nebius.sdk.SDK") as sdk,
        patch("nebius.aio.cli_config.Config") as config,
    ):
        client = build_operator_sdk_client(explicit_token="explicit-token")

    assert client is sdk.return_value
    config.assert_called_once_with(
        no_env=True,
        no_parent_id=True,
        profile="operator-profile",
        endpoint=None,
    )
    sdk.assert_called_once_with(
        credentials="explicit-token",
        config_reader=config.return_value,
        user_agent_prefix="nebius-vpngw",
    )


def test_operator_sdk_tokenless_path_uses_renewable_cli_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEBIUS_PROFILE", raising=False)
    monkeypatch.delenv("NEBIUS_ENDPOINT", raising=False)
    credentials = object()

    with (
        patch("nebius.sdk.SDK") as sdk,
        patch("nebius.aio.cli_config.Config") as config,
        patch(
            "nebius_vpngw.nebius_auth.renewable_cli_access_token_credentials",
            return_value=credentials,
        ) as renewable_credentials,
    ):
        client = build_operator_sdk_client(explicit_token=None)

    assert client is sdk.return_value
    renewable_credentials.assert_called_once_with()
    config.assert_called_once_with(
        no_env=True,
        no_parent_id=True,
        profile=None,
        endpoint=None,
    )
    sdk.assert_called_once_with(
        credentials=credentials,
        config_reader=config.return_value,
        user_agent_prefix="nebius-vpngw",
    )


def test_explicit_bearer_bypasses_incomplete_federation_credential_resolution(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "default: operator\n"
        "profiles:\n"
        "  operator:\n"
        "    auth-type: federation\n"
        "    endpoint: api.eu.nebius.cloud:443\n"
        "    federation-endpoint: https://auth.example.invalid\n",
        encoding="utf-8",
    )
    config = Config(config_file=config_path, no_env=True, no_parent_id=True)
    credentials = NebiusCLIAccessTokenCredentials()

    sdk = SDK(credentials=credentials, config_reader=config)
    try:
        assert sdk.get_authorization_provider() is not None
    finally:
        sdk.sync_close()
