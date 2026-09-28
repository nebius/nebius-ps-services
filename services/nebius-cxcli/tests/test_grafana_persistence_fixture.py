from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def fixture_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "grafana_persistence_fixture", scripts / "verify_grafana_persistence.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_observations_wait_for_database_after_each_transition(
    fixture_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = fixture_module
    state = {"ready": False, "probe_attempts": 0, "transitions": 0, "imports": 0, "logins": 0}
    saved = {"spec": {"title": "Imported source", "editable": True}}

    def docker(*args: str, stdin: str | None = None, timeout: float = 180) -> str:
        if (args[0] == "run" and module.POSTGRES in args) or (
            args[0] == "restart" and args[1].endswith("-pg")
        ):
            state.update(ready=False, probe_attempts=0, transitions=state["transitions"] + 1)
            if state["transitions"] == 3:
                assert stdin is None  # Reused data has no original container environment.
        if args[0] == "exec":
            state["probe_attempts"] += 1
            if state["probe_attempts"] == 1:
                raise RuntimeError("Database is still starting")
            state["ready"] = True
            return "1"
        if args[0] == "port":
            return "127.0.0.1:3000"
        return ""

    class Client:
        def __init__(self, *_args: object) -> None:
            pass

        def connect(self) -> None:
            assert state["ready"], "Cached HTTP health must not admit database observations"

        def get(self, _uid: str) -> dict:
            return saved

        def portable(self, dashboard: dict) -> dict:
            return dashboard["spec"]

        def write(self, dashboard: dict, *_args: object) -> dict:
            saved["spec"] = dashboard
            return saved

    def execute_imports(*_args: object) -> None:
        state["imports"] += 1

    def session_open(request: object, **_kwargs: object) -> io.BytesIO:
        if not isinstance(request, str):
            state["logins"] += 1
        response = io.BytesIO(b'{"login":"admin"}')
        response.status = 200
        return response

    monkeypatch.setattr(module, "docker", docker)
    monkeypatch.setattr(module, "GrafanaClient", Client)
    monkeypatch.setattr(module, "ready", lambda _url: None)  # Stale healthy cache throughout.
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module, "prepare_imports", lambda *_args: None)
    monkeypatch.setattr(module, "execute_imports", execute_imports)
    monkeypatch.setattr(module, "require_complete", lambda _result: None)
    monkeypatch.setattr(
        module.urllib.request, "build_opener", lambda *_args: SimpleNamespace(open=session_open)
    )
    monkeypatch.setattr(
        module.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout="", stderr="")
    )

    module.main()

    assert state["transitions"] == 3
    assert state["imports"] == 1
    assert state["logins"] == 1


@pytest.mark.parametrize("failure", ["starting", "timeout", "unexpected-result"])
def test_database_readiness_is_bounded_and_keeps_password_off_argv(
    fixture_module: ModuleType, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    module = fixture_module
    elapsed = 0.0
    attempts = 0
    password = "fixture-only-password"

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds

    def docker(*args: str, stdin: str | None = None, timeout: float = 180) -> str:
        nonlocal attempts
        attempts += 1
        assert password not in " ".join(args)
        assert args[:4] == ("exec", "--env-file", "/dev/stdin", "recreated-database")
        assert args[-2:] == ("-Atqc", "SELECT 1")
        assert stdin == f"PGPASSWORD={password}\n"
        assert 0 < timeout <= min(5, 90 - elapsed)
        if failure == "starting":
            raise RuntimeError("Database unavailable")
        if failure == "timeout":
            sleep(timeout)
            raise module.subprocess.TimeoutExpired(args, timeout)
        return ""

    monkeypatch.setattr(module, "docker", docker)
    monkeypatch.setattr(module.time, "monotonic", lambda: elapsed)
    monkeypatch.setattr(module.time, "sleep", sleep)

    with pytest.raises(RuntimeError, match="^PostgreSQL authenticated readiness timed out$"):
        module.postgres_ready("recreated-database", password)

    assert elapsed == 90
    assert attempts > 1
