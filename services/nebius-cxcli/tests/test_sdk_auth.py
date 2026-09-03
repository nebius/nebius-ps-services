from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from nebius_cxcli import sdk_auth


def _install_fake_nebius_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    nebius_module = ModuleType("nebius")
    sdk_module = ModuleType("nebius.sdk")
    aio_module = ModuleType("nebius.aio")
    cli_config_module = ModuleType("nebius.aio.cli_config")

    class FakeSDK:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.kwargs = kwargs
            self.closed = False

        def run_sync(self, awaitable, timeout=None):  # type: ignore[no-untyped-def]
            del timeout
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(awaitable)
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise RuntimeError("SDK run_sync rejects asynchronous callers")

        async def close(self, _grace=None):  # type: ignore[no-untyped-def]
            self.closed = True

        def sync_close(self, timeout=None):  # type: ignore[no-untyped-def]
            return self.run_sync(self.close(), timeout)

    class FakeConfig:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.kwargs = kwargs

    sdk_module.SDK = FakeSDK  # type: ignore[attr-defined]
    monkeypatch.setattr(cli_config_module, "Config", FakeConfig, raising=False)
    nebius_module.sdk = sdk_module  # type: ignore[attr-defined]
    aio_module.cli_config = cli_config_module  # type: ignore[attr-defined]
    nebius_module.aio = aio_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "nebius", nebius_module)
    monkeypatch.setitem(sys.modules, "nebius.sdk", sdk_module)
    monkeypatch.setitem(sys.modules, "nebius.aio", aio_module)
    monkeypatch.setitem(sys.modules, "nebius.aio.cli_config", cli_config_module)


def test_deleted_key_refresh_log_filter_only_suppresses_expected_traceback() -> None:
    expected_record = logging.LogRecord(
        "nebius.aio.token.renewable",
        logging.ERROR,
        __file__,
        1,
        "Failed refresh token, attempt: 1, error: Public Key not exists: JwtKeyNotExists",
        (),
        None,
    )
    unrelated_record = logging.LogRecord(
        "nebius.aio.token.renewable",
        logging.ERROR,
        __file__,
        1,
        "Failed refresh token, attempt: 1, error: network unavailable",
        (),
        None,
    )

    assert sdk_auth.deleted_key_refresh_log(expected_record)
    assert not sdk_auth.deleted_key_refresh_log(unrelated_record)


def test_retryable_refresh_log_filter_suppresses_deadline_exceeded_traceback() -> None:
    expected_record = logging.LogRecord(
        "nebius.aio.token.renewable",
        logging.ERROR,
        __file__,
        1,
        "Failed refresh token, attempt: 1, error: Request error DEADLINE_EXCEEDED: Deadline Exceeded",
        (),
        None,
    )
    unrelated_record = logging.LogRecord(
        "nebius.aio.token.renewable",
        logging.ERROR,
        __file__,
        1,
        "Failed refresh token, attempt: 1, error: invalid credentials",
        (),
        None,
    )

    assert sdk_auth.retryable_refresh_log(expected_record)
    assert not sdk_auth.retryable_refresh_log(unrelated_record)


def test_retryable_request_log_filter_suppresses_sdk_retry_traceback() -> None:
    expected_record = logging.LogRecord(
        "nebius.aio.request",
        logging.ERROR,
        __file__,
        1,
        "request attempt 1 for Request(.nebius.mk8s.v1.ClusterService.List) "
        "failed with Request error UNAVAILABLE: Received http2 header with status: 502 "
        "but will be retried",
        (),
        None,
    )
    unrelated_record = logging.LogRecord(
        "nebius.aio.request",
        logging.ERROR,
        __file__,
        1,
        "request failed permanently with Request error PERMISSION_DENIED",
        (),
        None,
    )

    assert sdk_auth.retryable_request_log(expected_record)
    assert not sdk_auth.retryable_request_log(unrelated_record)


def test_suppress_expected_sdk_retry_logs_filters_request_logger_only() -> None:
    request_logger = logging.getLogger("nebius.aio.request")
    refresh_logger = logging.getLogger("nebius.aio.token.renewable")
    request_filters_before = tuple(request_logger.filters)
    refresh_filters_before = tuple(refresh_logger.filters)

    with sdk_auth.suppress_expected_sdk_retry_logs():
        assert tuple(refresh_logger.filters) == refresh_filters_before
        assert len(request_logger.filters) == len(request_filters_before) + 1
        assert tuple(request_logger.filters[:-1]) == request_filters_before

    assert tuple(request_logger.filters) == request_filters_before
    assert tuple(refresh_logger.filters) == refresh_filters_before


def test_init_nebius_sdk_prefers_credentials_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)

    credentials_file = tmp_path / "auth.json"
    credentials_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NEBIUS_AUTH_CREDENTIALS_FILE", str(credentials_file))
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)

    sdk: Any = sdk_auth.init_nebius_sdk(parent_id="project-1", context="test")

    assert sdk.kwargs["credentials_file_name"] == credentials_file.resolve()
    assert sdk.kwargs["parent_id"] == "project-1"


def test_acquire_access_token_uses_existing_sdk_identity_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class _Token:
        token = "ephemeral-token"

    class _Sdk:
        def get_token_sync(self, timeout: float) -> _Token:
            calls.append(timeout)
            return _Token()

        def sync_close(self, timeout: float) -> None:
            calls.append(("closed", timeout))

    monkeypatch.setattr(
        sdk_auth,
        "init_nebius_sdk",
        lambda **kwargs: calls.append(kwargs) or _Sdk(),
    )

    token = sdk_auth.acquire_nebius_access_token(context="read-only-test")

    assert token == "ephemeral-token"
    assert isinstance(calls[0], dict)
    assert calls[0]["prefer_operator_auth"] is True
    assert calls[-1] == ("closed", 30.0)


def test_acquire_operator_access_token_ignores_runtime_service_account_and_token_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], bool, dict[str, str]]] = []
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "runtime-token")
    monkeypatch.setenv("CXCLI_NEBIUS_DELEGATE_ID", "serviceaccount-runtime")
    monkeypatch.setenv("NEBIUS_SA_ID", "serviceaccount-runtime")
    monkeypatch.setenv("NEBIUS_PROFILE", "operator-profile")

    def run(
        args: list[str],
        *,
        timeout_seconds: int,
        interactive: bool,
        env: dict[str, str],
    ):
        assert timeout_seconds == 30
        calls.append((args, interactive, env))
        return "operator-token", None

    monkeypatch.setattr(sdk_auth, "_run_iam_token_cli", run)

    token = sdk_auth.acquire_operator_access_token(interactive=False)

    assert token == "operator-token"
    assert calls == [
        (
            [
                "nebius",
                "iam",
                "get-access-token",
                "--profile",
                "operator-profile",
                "--format",
                "text",
                "--no-browser",
            ],
            False,
            {
                key: value
                for key, value in sdk_auth.os.environ.items()
                if key not in sdk_auth._OPERATOR_CLI_FORBIDDEN_AUTH_ENV
            },
        )
    ]
    assert "runtime-token" not in str(calls)
    assert "serviceaccount-runtime" not in str(calls)


def test_acquire_operator_access_token_noninteractive_never_retries_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object):
        calls.append(args)
        return None, sdk_auth._CliTokenUnavailable("sanitized failure")

    monkeypatch.setattr(sdk_auth, "_run_iam_token_cli", run)
    monkeypatch.setattr(sdk_auth, "_interactive_cli_auth_available", lambda: True)

    with pytest.raises(RuntimeError, match="browser reauthentication requires"):
        sdk_auth.acquire_operator_access_token(interactive=False)

    assert len(calls) == 1
    assert "--no-browser" in calls[0]


def test_acquire_operator_access_token_retries_browser_only_for_interactive_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], bool, dict[str, str]]] = []

    def run(
        args: list[str],
        *,
        timeout_seconds: int,
        interactive: bool,
        env: dict[str, str],
    ):
        del timeout_seconds
        calls.append((args, interactive, env))
        if interactive:
            return "browser-token", None
        return None, sdk_auth._CliTokenUnavailable("login required")

    monkeypatch.setattr(sdk_auth, "_run_iam_token_cli", run)
    monkeypatch.setattr(sdk_auth, "_interactive_cli_auth_available", lambda: True)

    token = sdk_auth.acquire_operator_access_token(interactive=True)

    assert token == "browser-token"
    assert len(calls) == 2
    assert "--no-browser" in calls[0][0]
    assert "--no-browser" not in calls[1][0]
    assert calls[1][1] is True
    assert calls[0][2] == calls[1][2]


def test_operator_token_subprocess_receives_sanitized_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    for name in sdk_auth._OPERATOR_CLI_FORBIDDEN_AUTH_ENV:
        monkeypatch.setenv(name, f"forbidden-{name.lower()}")
    monkeypatch.setenv("NEBIUS_PROFILE", "operator-profile")
    monkeypatch.setenv("CXCLI_OPERATOR_TEST_SENTINEL", "preserved")

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args, 0, "operator-token\n", "")

    monkeypatch.setattr(sdk_auth.subprocess, "run", run)

    token = sdk_auth.acquire_operator_access_token(interactive=False)

    assert token == "operator-token"
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["CXCLI_OPERATOR_TEST_SENTINEL"] == "preserved"
    assert env["NEBIUS_PROFILE"] == "operator-profile"
    assert sdk_auth._OPERATOR_CLI_FORBIDDEN_AUTH_ENV.isdisjoint(env)


def test_init_nebius_sdk_passes_endpoint_override_as_sdk_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)

    credentials_file = tmp_path / "auth.json"
    credentials_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NEBIUS_AUTH_CREDENTIALS_FILE", str(credentials_file))
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)

    sdk: Any = sdk_auth.init_nebius_sdk(
        endpoint="api.example.invalid",
        parent_id="project-1",
        context="test",
        require_renewable_auth=True,
    )

    assert sdk.kwargs["domain"] == "api.example.invalid"
    assert "endpoint" not in sdk.kwargs
    sdk.sync_close(timeout=2)


def test_init_nebius_sdk_uses_service_account_key_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)

    private_key_file = tmp_path / "auth-private.pem"
    private_key_file.write_text("PRIVATE-KEY", encoding="utf-8")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_SA_ID", "sa-1")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "pub-1")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(private_key_file))

    sdk: Any = sdk_auth.init_nebius_sdk(parent_id="project-1", context="test")

    assert sdk.kwargs["service_account_id"] == "sa-1"
    assert sdk.kwargs["service_account_public_key_id"] == "pub-1"
    assert sdk.kwargs["service_account_private_key_file_name"] == private_key_file.resolve()
    assert sdk.kwargs["parent_id"] == "project-1"


def test_init_nebius_sdk_uses_iam_token_env_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "iam-token-123")

    sdk: Any = sdk_auth.init_nebius_sdk(parent_id="project-1", context="test")

    assert sdk.kwargs["credentials"] == "iam-token-123"
    assert sdk.kwargs["parent_id"] == "project-1"
    assert sdk.kwargs["user_agent_prefix"] == sdk_auth.NEBIUS_SDK_USER_AGENT_PREFIX


def test_init_nebius_sdk_uses_sdk_owned_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "test-token")

    sdk: Any = sdk_auth.init_nebius_sdk(parent_id="project-test", context="test")
    try:
        assert sdk._runtime.owned  # noqa: SLF001
    finally:
        sdk.sync_close(timeout=5)


def test_init_nebius_sdk_keeps_native_async_context_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nebius.aio.channel import LoopError

    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "test-token")

    async def _run() -> None:
        sdk: Any = sdk_auth.init_nebius_sdk(parent_id="project-test", context="test")
        awaitable = asyncio.sleep(0)

        with pytest.raises(LoopError, match="asynchronous context"):
            sdk.run_sync(awaitable, timeout=2)

        await sdk.close()
        assert sdk._runtime.event_loop.is_closed()  # noqa: SLF001

    asyncio.run(_run())


def test_init_nebius_sdk_owned_runtime_finishes_work_before_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "test-token")
    caplog.set_level(logging.ERROR, logger="nebius.aio._runtime")

    for expected in range(20):
        sdk: Any = sdk_auth.init_nebius_sdk(parent_id="project-test", context="test")
        try:
            assert sdk._runtime.owned  # noqa: SLF001
            result = sdk.run_sync(asyncio.sleep(0, result=expected), timeout=5)
            assert result == expected
        finally:
            sdk.sync_close(timeout=5)
        assert sdk._runtime.event_loop.is_closed()  # noqa: SLF001

    assert not any(
        "The SDK runtime could not shut down" in record.getMessage() for record in caplog.records
    )


def test_init_nebius_sdk_does_not_supply_an_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "test-token")

    sdk: Any = sdk_auth.init_nebius_sdk(parent_id="project-test", context="test")

    assert "event_loop" not in sdk.kwargs


def test_init_nebius_sdk_fetches_iam_token_from_cli_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delitem(sys.modules, "nebius.aio.cli_config", raising=False)
    aio_module = sys.modules["nebius.aio"]
    if hasattr(aio_module, "cli_config"):
        delattr(aio_module, "cli_config")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)

    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: type("CP", (), {"stdout": "cli-token-456\n"})(),
    )

    sdk: Any = sdk_auth.init_nebius_sdk(parent_id="project-1", context="test")

    assert sdk.kwargs["credentials"] == "cli-token-456"
    assert sdk.kwargs["parent_id"] == "project-1"
    assert "NEBIUS_IAM_TOKEN" not in sdk_auth.os.environ


def test_init_nebius_sdk_uses_explicit_cli_impersonation_without_base_token_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.setenv("NEBIUS_PROFILE", "operator-profile")
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "base-identity-token")
    monkeypatch.setenv(
        "CXCLI_NEBIUS_DELEGATE_ID",
        "serviceaccount-operator",
    )
    calls: list[list[str]] = []

    def _token(*args: object, **kwargs: object) -> object:
        del kwargs
        calls.append(list(args[0]))  # type: ignore[arg-type]
        return type("CP", (), {"stdout": "impersonated-token\n"})()

    monkeypatch.setattr(sdk_auth.subprocess, "run", _token)

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="test",
        prefer_operator_auth=True,
    )

    assert sdk.kwargs["credentials"] == "impersonated-token"
    assert calls == [
        [
            "nebius",
            "iam",
            "get-access-token",
            "--profile",
            "operator-profile",
            "--impersonate-service-account-id",
            "serviceaccount-operator",
            "--format",
            "text",
            "--no-browser",
        ]
    ]


def test_init_nebius_sdk_does_not_drop_explicit_impersonation_when_cli_tokens_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.setenv(
        "CXCLI_NEBIUS_DELEGATE_ID",
        "serviceaccount-operator",
    )
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("CLI token exchange must remain disabled"),
    )

    with pytest.raises(RuntimeError, match="Failed to initialize Nebius SDK credentials"):
        sdk_auth.init_nebius_sdk(
            parent_id="project-1",
            context="test",
            allow_cli_token=False,
        )


def test_cli_token_fallback_does_not_mask_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)

    def _bug(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("unexpected bug")

    monkeypatch.setattr(sdk_auth.subprocess, "run", _bug)

    with pytest.raises(RuntimeError, match="unexpected bug"):
        sdk_auth._ensure_iam_token_from_cli()


def test_cli_token_fallback_refreshes_the_active_profile_interactively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(sdk_auth, "_interactive_cli_auth_available", lambda: True)
    calls: list[tuple[list[str], int]] = []

    def _token(args: list[str], **kwargs: object) -> object:
        calls.append((list(args), int(kwargs["timeout"])))
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, args, stderr="sensitive provider detail")
        return type("CP", (), {"stdout": "refreshed-cli-token\n"})()

    monkeypatch.setattr(sdk_auth.subprocess, "run", _token)

    token = sdk_auth._ensure_iam_token_from_cli(
        timeout_seconds=7,
        interactive_timeout_seconds=91,
    )

    assert token == "refreshed-cli-token"
    assert calls == [
        (["nebius", "iam", "get-access-token", "--format", "text", "--no-browser"], 7),
        (["nebius", "iam", "get-access-token", "--format", "text"], 91),
    ]


def test_init_nebius_sdk_reports_sanitized_noninteractive_cli_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)

    class FailingConfig:
        def __init__(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("config boom")

    cli_config_module = sys.modules["nebius.aio.cli_config"]
    monkeypatch.setattr(cli_config_module, "Config", FailingConfig)
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(sdk_auth, "_interactive_cli_auth_available", lambda: False)

    def _token(args: list[str], **_kwargs: object) -> object:
        raise subprocess.CalledProcessError(
            17,
            args,
            stderr="secret-bearing provider failure",
        )

    monkeypatch.setattr(sdk_auth.subprocess, "run", _token)

    with pytest.raises(RuntimeError, match="exit code 17") as exc_info:
        sdk_auth.init_nebius_sdk(
            parent_id="project-1",
            context="auth bootstrap",
            prefer_operator_auth=True,
        )

    message = str(exc_info.value)
    assert "stdin is not interactive" in message
    assert "secret-bearing provider failure" not in message
    assert isinstance(exc_info.value.__cause__, sdk_auth._CliTokenUnavailable)


@pytest.mark.parametrize(
    ("interactive_failure", "expected_detail"),
    [
        ("exit", "exit code 23"),
        ("timeout", "timed out after 91 seconds"),
    ],
)
def test_cli_token_browser_failure_is_sanitized_and_preserves_identity_args(
    monkeypatch: pytest.MonkeyPatch,
    interactive_failure: str,
    expected_detail: str,
) -> None:
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setenv("NEBIUS_PROFILE", "operator-profile")
    monkeypatch.setenv("CXCLI_NEBIUS_DELEGATE_ID", "serviceaccount-delegate")
    monkeypatch.setattr(sdk_auth, "_interactive_cli_auth_available", lambda: True)
    calls: list[tuple[list[str], int]] = []

    def _token(args: list[str], **kwargs: object) -> object:
        timeout = int(kwargs["timeout"])
        calls.append((list(args), timeout))
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, args)
        if interactive_failure == "timeout":
            raise subprocess.TimeoutExpired(
                args,
                timeout,
                output="sensitive browser output",
                stderr="sensitive browser error",
            )
        raise subprocess.CalledProcessError(
            23,
            args,
            output="sensitive browser output",
            stderr="sensitive browser error",
        )

    monkeypatch.setattr(sdk_auth.subprocess, "run", _token)

    token, failure = sdk_auth._iam_token_from_cli_attempt(
        timeout_seconds=7,
        interactive_timeout_seconds=91,
    )

    assert token is None
    assert failure is not None
    assert expected_detail in str(failure)
    assert "sensitive browser" not in str(failure)
    common_args = [
        "nebius",
        "iam",
        "get-access-token",
        "--profile",
        "operator-profile",
        "--impersonate-service-account-id",
        "serviceaccount-delegate",
        "--format",
        "text",
    ]
    assert calls == [
        ([*common_args, "--no-browser"], 7),
        (common_args, 91),
    ]


def test_init_nebius_sdk_can_disable_cli_token_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delitem(sys.modules, "nebius.aio.cli_config", raising=False)
    aio_module = sys.modules["nebius.aio"]
    if hasattr(aio_module, "cli_config"):
        delattr(aio_module, "cli_config")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("CLI token fallback should be disabled"),
    )

    with pytest.raises(RuntimeError, match="Failed to initialize Nebius SDK credentials"):
        sdk_auth.init_nebius_sdk(
            parent_id="project-1",
            context="test",
            allow_cli_token=False,
        )


def test_init_nebius_sdk_falls_back_to_sdk_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("nebius not found")),
    )

    sdk: Any = sdk_auth.init_nebius_sdk(
        profile="dev",
        endpoint="api.example.invalid",
        parent_id="project-1",
        context="test",
    )

    assert sdk.kwargs["parent_id"] == "project-1"
    config = sdk.kwargs["config_reader"]
    assert config.kwargs == {"profile": "dev", "endpoint": "api.example.invalid"}


def test_init_nebius_sdk_uses_profile_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setenv("NEBIUS_PROFILE", "codex-agent-project-1")
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("CLI token should not be needed"),
    )

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="test",
        prefer_operator_auth=True,
    )

    config = sdk.kwargs["config_reader"]
    assert config.kwargs == {"profile": "codex-agent-project-1"}


def test_init_nebius_sdk_prefer_operator_auth_uses_iam_token_before_runtime_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    private_key_file = tmp_path / "auth-private.pem"
    private_key_file.write_text("PRIVATE-KEY", encoding="utf-8")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_SA_ID", "sa-1")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "pub-1")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(private_key_file))
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "operator-token-123")
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("CLI token should not be needed"),
    )

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="quota assessment",
        prefer_operator_auth=True,
    )

    assert sdk.kwargs["credentials"] == "operator-token-123"
    assert "service_account_id" not in sdk.kwargs
    assert sdk.kwargs["parent_id"] == "project-1"


def test_init_nebius_sdk_prefer_operator_auth_prefers_credentials_file_over_iam_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    credentials_file = tmp_path / "codex-agent-authkey.project-1.json"
    credentials_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NEBIUS_AUTH_CREDENTIALS_FILE", str(credentials_file))
    monkeypatch.setenv("NEBIUS_PROFILE", "codex-agent-project-1")
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "operator-token-123")
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("CLI token should not be needed"),
    )

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="quota assessment",
        prefer_operator_auth=True,
    )

    assert sdk.kwargs["credentials_file_name"] == credentials_file.resolve()
    assert "credentials" not in sdk.kwargs
    assert sdk.kwargs["parent_id"] == "project-1"


def test_init_nebius_sdk_prefer_operator_auth_ignores_missing_codex_credentials_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    credentials_file = tmp_path / "codex-agent-authkey.project-1.json"
    monkeypatch.setenv("NEBIUS_AUTH_CREDENTIALS_FILE", str(credentials_file))
    monkeypatch.setenv("NEBIUS_PROFILE", "codex-agent-project-1")
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "operator-token-123")
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("CLI token should not be needed"),
    )

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="quota assessment",
        prefer_operator_auth=True,
    )

    assert sdk.kwargs["credentials"] == "operator-token-123"
    assert "credentials_file_name" not in sdk.kwargs
    assert sdk.kwargs["parent_id"] == "project-1"


def test_init_nebius_sdk_prefer_operator_auth_keeps_generic_credentials_after_iam_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    credentials_file = tmp_path / "runtime-auth.json"
    credentials_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NEBIUS_AUTH_CREDENTIALS_FILE", str(credentials_file))
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "operator-token-123")
    monkeypatch.delenv("NEBIUS_PROFILE", raising=False)
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("CLI token should not be needed"),
    )

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="quota assessment",
        prefer_operator_auth=True,
    )

    assert sdk.kwargs["credentials"] == "operator-token-123"
    assert "credentials_file_name" not in sdk.kwargs
    assert sdk.kwargs["parent_id"] == "project-1"


def test_init_nebius_sdk_prefer_operator_auth_keeps_mismatched_codex_file_after_iam_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    credentials_file = tmp_path / "codex-agent-authkey.project-other.json"
    credentials_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NEBIUS_AUTH_CREDENTIALS_FILE", str(credentials_file))
    monkeypatch.setenv("NEBIUS_PROFILE", "codex-agent-project-1")
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "operator-token-123")
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("CLI token should not be needed"),
    )

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="quota assessment",
        prefer_operator_auth=True,
    )

    assert sdk.kwargs["credentials"] == "operator-token-123"
    assert "credentials_file_name" not in sdk.kwargs
    assert sdk.kwargs["parent_id"] == "project-1"


def test_init_nebius_sdk_prefer_operator_auth_uses_sdk_config_before_runtime_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    private_key_file = tmp_path / "auth-private.pem"
    private_key_file.write_text("PRIVATE-KEY", encoding="utf-8")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_SA_ID", "sa-1")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "pub-1")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(private_key_file))
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("SDK config should be tried before CLI token"),
    )

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="quota assessment",
        prefer_operator_auth=True,
    )

    assert "config_reader" in sdk.kwargs
    assert "service_account_id" not in sdk.kwargs
    assert sdk.kwargs["parent_id"] == "project-1"


def test_init_nebius_sdk_prefer_operator_auth_falls_back_to_service_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delitem(sys.modules, "nebius.aio.cli_config", raising=False)
    aio_module = sys.modules["nebius.aio"]
    if hasattr(aio_module, "cli_config"):
        delattr(aio_module, "cli_config")
    private_key_file = tmp_path / "auth-private.pem"
    private_key_file.write_text("PRIVATE-KEY", encoding="utf-8")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_SA_ID", "sa-1")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "pub-1")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(private_key_file))
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("nebius not found")),
    )

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="quota assessment",
        prefer_operator_auth=True,
    )

    assert sdk.kwargs["service_account_id"] == "sa-1"
    assert sdk.kwargs["service_account_public_key_id"] == "pub-1"
    assert sdk.kwargs["service_account_private_key_file_name"] == private_key_file.resolve()
    assert sdk.kwargs["parent_id"] == "project-1"


def test_init_nebius_sdk_prefer_operator_auth_uses_cli_token_before_runtime_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)

    class FailingConfig:
        def __init__(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("config boom")

    cli_config_module = sys.modules["nebius.aio.cli_config"]
    monkeypatch.setattr(cli_config_module, "Config", FailingConfig)
    private_key_file = tmp_path / "auth-private.pem"
    private_key_file.write_text("PRIVATE-KEY", encoding="utf-8")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_SA_ID", "sa-1")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "pub-1")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(private_key_file))
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: type("CP", (), {"stdout": "cli-token-789\n"})(),
    )

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="quota assessment",
        prefer_operator_auth=True,
    )

    assert sdk.kwargs["credentials"] == "cli-token-789"
    assert "service_account_id" not in sdk.kwargs


def test_init_nebius_sdk_prefer_operator_auth_refreshes_active_cli_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)

    class FailingConfig:
        def __init__(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("federation profile is not SDK-readable")

    cli_config_module = sys.modules["nebius.aio.cli_config"]
    monkeypatch.setattr(cli_config_module, "Config", FailingConfig)
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)
    monkeypatch.setattr(sdk_auth, "_interactive_cli_auth_available", lambda: True)
    calls: list[list[str]] = []

    def _token(args: list[str], **_kwargs: object) -> object:
        calls.append(list(args))
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, args)
        return type("CP", (), {"stdout": "refreshed-operator-token\n"})()

    monkeypatch.setattr(sdk_auth.subprocess, "run", _token)

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="auth bootstrap",
        prefer_operator_auth=True,
    )

    assert sdk.kwargs["credentials"] == "refreshed-operator-token"
    assert calls[0][-1] == "--no-browser"
    assert "--no-browser" not in calls[1]


def test_init_nebius_sdk_chains_failed_config_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)

    class FailingConfig:
        def __init__(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("config boom")

    cli_config_module = sys.modules["nebius.aio.cli_config"]
    monkeypatch.setattr(cli_config_module, "Config", FailingConfig)
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_IAM_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="Failed to initialize") as exc_info:
        sdk_auth.init_nebius_sdk(
            parent_id="project-1",
            context="quota assessment",
            allow_cli_token=False,
        )

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "config boom"


def test_init_nebius_sdk_renewable_auth_ignores_static_token_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    private_key_file = tmp_path / "auth-private.pem"
    private_key_file.write_text("PRIVATE-KEY", encoding="utf-8")
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_SA_ID", "sa-1")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "pub-1")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(private_key_file))
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "static-token")
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("CLI token must not satisfy renewable auth"),
    )

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="long migration",
        require_renewable_auth=True,
    )

    assert sdk.kwargs["service_account_id"] == "sa-1"
    assert sdk.kwargs["service_account_public_key_id"] == "pub-1"
    assert sdk.kwargs["service_account_private_key_file_name"] == private_key_file.resolve()
    assert "credentials" not in sdk.kwargs
    assert "config_reader" not in sdk.kwargs


def test_init_nebius_sdk_renewable_auth_prefers_credentials_file_over_static_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    credentials_file = tmp_path / "auth.json"
    credentials_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NEBIUS_AUTH_CREDENTIALS_FILE", str(credentials_file))
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "static-token")

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="long migration",
        require_renewable_auth=True,
    )

    assert sdk.kwargs["credentials_file_name"] == credentials_file.resolve()
    assert "credentials" not in sdk.kwargs


def test_init_nebius_sdk_renewable_auth_falls_back_from_stale_file_to_service_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    private_key_file = tmp_path / "auth-private.pem"
    private_key_file.write_text("PRIVATE-KEY", encoding="utf-8")
    monkeypatch.setenv("NEBIUS_AUTH_CREDENTIALS_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("NEBIUS_SA_ID", "sa-1")
    monkeypatch.setenv("NEBIUS_AUTH_PUBLIC_KEY_ID", "pub-1")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", str(private_key_file))

    sdk: Any = sdk_auth.init_nebius_sdk(
        parent_id="project-1",
        context="long migration",
        require_renewable_auth=True,
    )

    assert sdk.kwargs["service_account_id"] == "sa-1"
    assert sdk.kwargs["service_account_private_key_file_name"] == private_key_file.resolve()


def test_init_nebius_sdk_renewable_auth_rejects_static_only_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.delenv("NEBIUS_AUTH_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "static-token")
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("CLI token must not satisfy renewable auth"),
    )

    with pytest.raises(RuntimeError, match="canonical nebius-cxcli-sa profile automatically"):
        sdk_auth.init_nebius_sdk(
            parent_id="project-1",
            context="long migration",
            require_renewable_auth=True,
        )


def test_init_nebius_sdk_renewable_auth_reports_set_variable_names_and_last_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    credentials_file = tmp_path / "sensitive-auth-path.json"
    credentials_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NEBIUS_AUTH_CREDENTIALS_FILE", str(credentials_file))
    monkeypatch.delenv("NEBIUS_SA_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", raising=False)
    sdk_module = sys.modules["nebius.sdk"]

    class FailingSDK:
        def __init__(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise TypeError("SDK constructor rejected the supplied domain")

    monkeypatch.setattr(sdk_module, "SDK", FailingSDK)

    with pytest.raises(RuntimeError, match="Failed to initialize renewable") as exc_info:
        sdk_auth.init_nebius_sdk(
            parent_id="project-1",
            context="long migration",
            require_renewable_auth=True,
        )

    assert "NEBIUS_AUTH_CREDENTIALS_FILE" in str(exc_info.value)
    assert str(credentials_file) not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, TypeError)
    assert str(exc_info.value.__cause__) == "SDK constructor rejected the supplied domain"


def test_init_nebius_sdk_renewable_auth_rejects_cli_impersonation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_nebius_modules(monkeypatch)
    monkeypatch.setenv("CXCLI_NEBIUS_DELEGATE_ID", "serviceaccount-operator")
    monkeypatch.setattr(
        sdk_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("impersonation token must not be snapshotted"),
    )

    with pytest.raises(RuntimeError, match="renewable Nebius SDK credentials"):
        sdk_auth.init_nebius_sdk(
            parent_id="project-1",
            context="long migration",
            require_renewable_auth=True,
        )
