from __future__ import annotations

import importlib
import os

import nebius_cxcli.native_logs as native_logs


def test_native_logs_sets_defaults_when_env_missing(monkeypatch) -> None:
    for env_name in native_logs.QUIET_NATIVE_LOG_ENV_DEFAULTS:
        monkeypatch.delenv(env_name, raising=False)

    reloaded = importlib.reload(native_logs)

    for env_name, env_value in reloaded.QUIET_NATIVE_LOG_ENV_DEFAULTS.items():
        assert os.environ[env_name] == env_value


def test_native_logs_does_not_override_existing_env(monkeypatch) -> None:
    expected = {
        "GRPC_VERBOSITY": "DEBUG",
        "GLOG_minloglevel": "1",
        "ABSL_LOG_SEVERITY_LEVEL": "0",
        "GRPC_ENABLE_FORK_SUPPORT": "1",
    }
    for env_name, env_value in expected.items():
        monkeypatch.setenv(env_name, env_value)

    reloaded = importlib.reload(native_logs)

    for env_name, env_value in expected.items():
        assert os.environ[env_name] == env_value
    for env_name in expected:
        assert env_name in reloaded.QUIET_NATIVE_LOG_ENV_DEFAULTS
