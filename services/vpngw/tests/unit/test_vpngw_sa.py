from __future__ import annotations

import sys
import types
import typing as t
from unittest.mock import Mock, patch

from nebius_vpngw.vpngw_sa import ensure_cli_access_token, get_cli_token


def test_get_cli_token_reads_token_attribute(monkeypatch) -> None:
    nebius_module = types.ModuleType("nebius")
    aio_module = types.ModuleType("nebius.aio")
    cli_config_module = types.ModuleType("nebius.aio.cli_config")

    class FakeConfig:
        def __init__(self, no_parent_id: bool = True) -> None:
            self.no_parent_id = no_parent_id
            self.token = "cli-token"

    t.cast(t.Any, cli_config_module).Config = FakeConfig
    t.cast(t.Any, aio_module).cli_config = cli_config_module
    t.cast(t.Any, nebius_module).aio = aio_module

    monkeypatch.setitem(sys.modules, "nebius", nebius_module)
    monkeypatch.setitem(sys.modules, "nebius.aio", aio_module)
    monkeypatch.setitem(sys.modules, "nebius.aio.cli_config", cli_config_module)

    assert get_cli_token() == "cli-token"


def test_ensure_cli_access_token_falls_back_to_cli_json() -> None:
    class FakeFuture:
        def result(self, timeout: int):
            return None

    class FakeExecutor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn):
            return FakeFuture()

    with (
        patch("nebius_vpngw.vpngw_sa.get_cli_token", return_value=None),
        patch("nebius_vpngw.vpngw_sa.ThreadPoolExecutor", return_value=FakeExecutor()),
        patch(
            "nebius_vpngw.vpngw_sa.subprocess.run",
            return_value=Mock(returncode=0, stdout='{"access_token":"token-123"}', stderr=""),
        ) as run_mock,
    ):
        token = ensure_cli_access_token(timeout_seconds=1)

    assert token == "token-123"
    assert "--format" in run_mock.call_args[0][0]
