from __future__ import annotations

import logging

import pytest
from typer.testing import CliRunner

from nebius_cxcli import cli, sdk_auth


@pytest.mark.parametrize("outcome", ["success", "failure", "interrupt"])
def test_command_scopes_refresh_diagnostics_and_preserves_outcome(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    outcome: str,
) -> None:
    logger = logging.getLogger("nebius.aio.token.renewable")
    before = tuple(logger.filters)

    def validate(**_kwargs):
        try:
            raise TimeoutError()
        except TimeoutError:
            logger.exception("Failed refresh token, attempt: 1, error: ")
        if outcome == "failure":
            raise RuntimeError("Authentication request exhausted its deadline")
        if outcome == "interrupt":
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run_runtime_validation", validate)
    result = CliRunner().invoke(cli.app, ["validate", "config.yaml"])

    assert result.exit_code == {"success": 0, "failure": 1, "interrupt": 130}[outcome]
    if outcome == "failure":
        assert "Authentication request exhausted its deadline" in result.output
    assert tuple(logger.filters) == before
    records = [r for r in caplog.records if r.name == logger.name]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].exc_info is None
    assert "token refresh timed out" in caplog.text
    assert "Traceback" not in caplog.text


def test_concise_refresh_record_removes_provider_text_and_cached_traceback() -> None:
    error = TimeoutError("sensitive-provider-detail")
    record = logging.LogRecord(
        "nebius.aio.token.renewable",
        logging.ERROR,
        __file__,
        1,
        "Failed refresh token, attempt: 1, error: %s",
        (error,),
        (type(error), error, None),
    )
    logging.Formatter().format(record)
    original = record.__dict__.copy()
    with sdk_auth.concise_refresh_logs():
        rendered = logging.getLogger(record.name).filter(record)
    assert isinstance(rendered, logging.LogRecord)
    assert rendered is not record
    assert record.__dict__ == original
    for field in (rendered.getMessage(), rendered.message, logging.Formatter().format(rendered)):
        assert "sensitive-provider-detail" not in field
    assert rendered.exc_info is None
    assert rendered.exc_text is None
    assert rendered.stack_info is None
