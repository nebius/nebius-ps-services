from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def wheel_verifier():
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_wheel_cli.py"
    spec = importlib.util.spec_from_file_location("wheel_verifier_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_soperator_wheel_smoke_accepts_real_sanitized_status_failure(
    wheel_verifier, tmp_path: Path
) -> None:
    wheel_verifier._verify_soperator_semantics(tmp_path)


@pytest.mark.parametrize(
    "fault",
    [
        "unreached",
        "wrong-config",
        "leaked-error",
        "missing-overall",
        "missing-diagnostic",
        "successful-exit",
    ],
)
def test_soperator_wheel_smoke_rejects_incomplete_status_evidence(
    wheel_verifier, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    runner = wheel_verifier.TyperCliRunner()

    def invoke(app, argv):
        if argv[:2] != ["soperator", "status"]:
            return runner.invoke(app, argv)
        if fault != "unreached":
            path = Path(argv[2])
            if fault == "wrong-config":
                path = path.with_name("wrong-config.yaml")
            with pytest.raises(RuntimeError):
                wheel_verifier.cli._read_config_payload(path)
        diagnostic = "Status collection failed; verify configuration, cluster identity and access."
        overall = "Overall health: Error — Status could not safely complete"
        output = "\n".join(
            part
            for part, omit in (
                (diagnostic, fault == "missing-diagnostic"),
                (overall, fault == "missing-overall"),
            )
            if not omit
        )
        if fault == "leaked-error":
            output += "\ninstalled-wheel-status-callback"
        return SimpleNamespace(exit_code=0 if fault == "successful-exit" else 1, output=output)

    monkeypatch.setattr(wheel_verifier, "TyperCliRunner", lambda: SimpleNamespace(invoke=invoke))
    with pytest.raises((AssertionError, RuntimeError)):
        wheel_verifier._verify_soperator_semantics(tmp_path)
