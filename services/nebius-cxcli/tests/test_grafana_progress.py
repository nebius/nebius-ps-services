from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from nebius_cxcli import grafana_progress
from nebius_cxcli.grafana_progress import GrafanaProgress


def renderer(terminal=False):
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=terminal,
        no_color=True,
        width=100,
        _environ={"TERM": "xterm"},
    )
    return GrafanaProgress(console), output


def test_redirected_progress_is_bounded_plain_text_and_deduplicates_stages():
    progress, output = renderer()
    with progress:
        progress.update("Loading dashboard files")
        for _ in range(100):
            progress.update("Loading dashboard files")
        progress.update("Checking [literal] input")
    assert output.getvalue().splitlines() == [
        "Grafana import: Loading dashboard files",
        "Grafana import: Checking [literal] input",
    ]
    assert "\x1b" not in output.getvalue()


def test_terminal_pause_is_reentrant_and_close_does_not_restart_display():
    progress, _ = renderer(terminal=True)
    with progress:
        progress.update("Connecting to Grafana")
        display = progress._display
        assert display.live.is_started
        with progress.paused():
            assert not display.live.is_started
            with progress.paused():
                assert not display.live.is_started
            assert not display.live.is_started
        assert display.live.is_started
        with progress.paused():
            progress.close()
        assert not display.live.is_started
    assert not display.live.is_started


def test_prompt_pause_resumes_only_when_the_next_work_stage_starts():
    progress, _ = renderer(terminal=True)
    with progress:
        progress.update("Waiting for datasource selection")
        display = progress._display
        with progress.paused(resume=False):
            assert not display.live.is_started
        with progress.paused():  # Printing the selected mapping does not restart the waiting row.
            assert not display.live.is_started
        assert not display.live.is_started
        progress.update("Validating dashboards")
        assert display.live.is_started


@pytest.mark.parametrize("failure", [RuntimeError("operation failed"), KeyboardInterrupt()])
def test_failure_and_cancellation_stop_renderer_without_success(failure):
    progress, output = renderer(terminal=True)
    with pytest.raises(type(failure)), progress:
        progress.update("Acquiring project operation lease")
        with progress.paused():
            raise failure
    assert not progress._display.live.is_started
    assert "stopped during: Acquiring project operation lease" in output.getvalue()
    assert "Verified" not in output.getvalue()


@pytest.mark.parametrize("terminal", [False, True])
def test_broken_renderer_does_not_change_operation_outcome(monkeypatch, terminal):
    progress, _ = renderer(terminal)

    def broken(*_args, **_kwargs):
        raise OSError("closed stream")

    if terminal:
        monkeypatch.setattr(grafana_progress, "Progress", broken)
    else:
        monkeypatch.setattr(progress.console, "print", broken)
    completed = []
    with progress:
        progress.update("Connecting to Grafana")
        with progress.paused():
            completed.append(True)
        progress.update("Verifying dashboards")
    assert completed == [True]


def test_progress_does_not_redirect_result_stdout(capsys):
    with GrafanaProgress() as progress:
        progress.update("Connecting to Grafana")
        with progress.paused():
            print("Installed: fixture")
    streams = capsys.readouterr()
    assert streams.out == "Installed: fixture\n"
    assert streams.err == "Grafana import: Connecting to Grafana\n"
