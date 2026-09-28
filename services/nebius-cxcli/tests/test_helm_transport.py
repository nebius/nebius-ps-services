from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from nebius_cxcli.helm_client import _run_helm_show


@pytest.mark.parametrize("subcommand", ["chart", "values"])
@pytest.mark.parametrize(
    "failure",
    [
        "connection reset by peer",
        "i/o timeout",
        "process timeout",
        "read tcp 192.0.2.1:54010->192.0.2.2:443: connection reset by peer",
        "read tcp 192.0.2.1:54030->192.0.2.2:443: connection reset by peer",
        "read tcp 192.0.2.1:54040->192.0.2.2:443: connection reset by peer",
    ],
)
def test_helm_show_recovers_from_transient_failure(
    monkeypatch: pytest.MonkeyPatch, subcommand: str, failure: str
) -> None:
    commands: list[list[str]] = []
    delays: list[float] = []

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        if len(commands) == 1:
            if failure == "process timeout":
                raise subprocess.TimeoutExpired(command, 90, stderr=b"sensitive transport detail")
            return SimpleNamespace(returncode=1, stdout="", stderr=failure)
        return SimpleNamespace(returncode=0, stdout="name: demo\nversion: 1.0.0\n", stderr="")

    monkeypatch.setattr("nebius_cxcli.helm_client.kubernetes_process.run", run)
    monkeypatch.setattr("time.sleep", delays.append)

    output = _run_helm_show(subcommand, "oci://registry.example.test/demo", version="1.0.0")

    assert "name: demo" in output
    assert len(commands) == 2
    assert commands[0] == commands[1]
    assert commands[0][-2:] == ["--version", "1.0.0"]
    assert len(delays) == 1 and 0 < delays[0] <= 1.25


def test_helm_show_exhaustion_is_bounded_and_does_not_expose_signed_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    delays: list[float] = []

    def run(*args: object, **_kwargs: object) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr='Get "https://storage.example.test/blob?X-Amz-Signature=test-signature": '
            "read tcp 192.0.2.1:1234->192.0.2.2:443: read: connection reset by peer",
        )

    monkeypatch.setattr("nebius_cxcli.helm_client.kubernetes_process.run", run)
    monkeypatch.setattr("time.sleep", delays.append)

    with pytest.raises(RuntimeError, match="3 attempts.*connection reset") as error:
        _run_helm_show("chart", "oci://registry.example.test/demo", version="1.0.0")

    assert len(calls) == 3
    assert len(delays) == 2 and sum(delays) <= 3.5
    assert "test-signature" not in str(error.value)
    assert "192.0.2" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


@pytest.mark.parametrize(
    "failure",
    [
        "unauthorized",
        "403 Forbidden",
        "x509: certificate expired",
        "404 not found",
        "digest mismatch",
        "unauthorized after connection reset",
        "unclassified error",
        "unexpected status: 401 after connection reset",
    ],
)
def test_helm_show_permanent_errors_fail_without_retry_and_redact_urls(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    calls: list[object] = []

    def run(*args: object, **_kwargs: object) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=f"{failure}: https://storage.example.test/blob?X-Amz-Signature=test-signature",
        )

    monkeypatch.setattr("nebius_cxcli.helm_client.kubernetes_process.run", run)
    monkeypatch.setattr(
        "time.sleep", lambda _delay: pytest.fail("permanent failure must not retry")
    )

    with pytest.raises(RuntimeError, match=failure) as error:
        _run_helm_show("chart", "oci://registry.example.test/demo", version="1.0.0")

    assert len(calls) == 1
    assert "test-signature" not in str(error.value)
    assert "X-Amz" not in str(error.value)


def test_helm_show_timeout_exhaustion_drops_raw_exception_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def run(command: list[str], *, timeout: int, **_kwargs: object) -> None:
        calls.append(timeout)
        raise subprocess.TimeoutExpired(command, timeout, stderr=b"sensitive detail")

    monkeypatch.setenv("NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS", "7")
    monkeypatch.setattr("nebius_cxcli.helm_client.kubernetes_process.run", run)
    monkeypatch.setattr("time.sleep", lambda _delay: None)

    with pytest.raises(RuntimeError, match="3 attempts.*network timeout") as error:
        _run_helm_show("chart", "oci://registry.example.test/demo", version="1.0.0")

    assert calls == [7, 7, 7]
    assert "sensitive detail" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_helm_show_does_not_classify_query_text_as_a_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.helm_client.kubernetes_process.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="failed: https://storage.example.test/blob?reason=connection reset",
        ),
    )
    monkeypatch.setattr("time.sleep", lambda _delay: pytest.fail("unexpected retry"))

    with pytest.raises(RuntimeError, match="failed:"):
        _run_helm_show("chart", "oci://registry.example.test/demo", version="1.0.0")
