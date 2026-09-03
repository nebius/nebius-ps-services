from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from nebius_cxcli.soperator_upgrade_progress import (
    SoperatorProgressEvent,
    SoperatorProgressState,
    SoperatorUpgradeProgress,
    rendered_flux_progress_surface,
)


def test_terminal_progress_replaces_spinner_slot_with_green_success() -> None:
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=True,
        _environ={"TERM": "xterm"},
        color_system="standard",
        width=120,
    )
    renderer = SoperatorUpgradeProgress(console, terminal=True)

    with renderer.sequence() as sequence:
        sequence.start(
            "flux-rollout",
            "Flux target controllers — source-controller",
            current=1,
            total=4,
        )
        sequence.handle(
            SoperatorProgressEvent(
                phase="flux-rollout",
                state=SoperatorProgressState.UPDATE,
                description="Flux target controllers — notification-controller",
                current=4,
                total=4,
            )
        )
        sequence.handle(
            SoperatorProgressEvent(
                phase="flux-rollout",
                state=SoperatorProgressState.SUCCESS,
                description="Flux target controllers ready",
                current=4,
                total=4,
            )
        )

    rendered = output.getvalue()
    assert "✓" in rendered
    assert "Flux target controllers ready" in rendered
    assert "(4/4)" in rendered
    assert "✓ ✓" not in rendered


def test_terminal_progress_commits_rows_and_bounds_the_live_surface() -> None:
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=True,
        _environ={"TERM": "xterm"},
        color_system="standard",
        width=120,
    )
    renderer = SoperatorUpgradeProgress(console, terminal=True)

    with renderer.sequence() as sequence:
        for index in range(24):
            sequence.start(f"phase-{index}", f"Running phase {index}")
            sequence.success(f"Completed phase {index}")
            progress = sequence._progress  # noqa: SLF001
            assert progress is not None
            assert progress.tasks == []

    rendered = output.getvalue()
    for index in range(24):
        assert f"Completed phase {index}" in rendered
    assert "✓" in rendered
    assert "0:00:00" in rendered


def test_progress_notice_uses_the_progress_console() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, no_color=True)
    renderer = SoperatorUpgradeProgress(console, terminal=False)

    renderer.message("Retrying the same durable campaign")

    assert output.getvalue() == "Retrying the same durable campaign\n"


def test_terminal_retry_wait_stays_live_with_spinner_and_elapsed_time() -> None:
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=True,
        _environ={"TERM": "xterm"},
        color_system="standard",
        width=160,
    )
    renderer = SoperatorUpgradeProgress(console, terminal=True)

    with renderer.retry_wait(
        "full-stack-retry-wait",
        "Campaign safety-paused; waiting 0.9s before retry",
        success="Retry delay complete; starting attempt 2",
    ) as sequence:
        progress = sequence._progress  # noqa: SLF001
        assert progress is not None
        assert progress.live._started is True  # noqa: SLF001
        assert len(progress.tasks) == 1
        assert progress.tasks[0].fields["state"] == SoperatorProgressState.RETRY

    assert progress.tasks == []
    assert progress.live._started is False  # noqa: SLF001

    rendered = output.getvalue()
    assert "Campaign safety-paused; waiting 0.9s before retry" in rendered
    assert "Retry delay complete; starting attempt 2" in rendered
    assert "0:00:00" in rendered
    assert "✓" in rendered


def test_non_terminal_retry_wait_emits_one_retry_record_without_ansi() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, no_color=True, width=120)
    renderer = SoperatorUpgradeProgress(console, terminal=False)

    with renderer.retry_wait(
        "full-stack-retry-wait",
        "Campaign safety-paused; waiting before retry",
        success="Retry delay complete",
    ):
        pass

    assert output.getvalue().splitlines() == [
        "Soperator upgrade: RETRY Campaign safety-paused; waiting before retry",
        "Soperator upgrade: OK Retry delay complete",
    ]
    assert "\x1b" not in output.getvalue()


def test_terminal_progress_pause_is_reentrant_and_restarts_after_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=True,
        _environ={"TERM": "xterm"},
        color_system="standard",
        width=120,
    )
    renderer = SoperatorUpgradeProgress(console, terminal=True)

    with renderer.sequence() as sequence:
        sequence.start("maintenance-entry", "Entering durable Slurm maintenance")
        progress = sequence._progress  # noqa: SLF001
        assert progress is not None
        assert progress.live._started is True  # noqa: SLF001
        lifecycle: list[tuple[str, bool]] = []
        original_stop = progress.stop
        original_start = progress.start

        def _stop() -> None:
            lifecycle.append(("stop", progress.live.transient))
            original_stop()

        def _start() -> None:
            lifecycle.append(("start", progress.live.transient))
            original_start()

        monkeypatch.setattr(progress, "stop", _stop)
        monkeypatch.setattr(progress, "start", _start)

        with (
            pytest.raises(RuntimeError, match="nested dashboard failed"),
            sequence.paused(),
        ):
            assert progress.live._started is False  # noqa: SLF001
            with sequence.paused():
                assert progress.live._started is False  # noqa: SLF001
            raise RuntimeError("nested dashboard failed")

        assert progress.live._started is True  # noqa: SLF001
        assert lifecycle == [("stop", True), ("start", False)]
        sequence.failure("Maintenance entry failed")

    rendered = output.getvalue()
    assert "✗" in rendered
    assert "Maintenance entry failed" in rendered
    assert "✓" not in rendered


@pytest.mark.parametrize("failure_method", ["stop", "start"])
def test_terminal_progress_pause_renderer_failure_is_presentation_only(
    monkeypatch: pytest.MonkeyPatch,
    failure_method: str,
) -> None:
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=True,
        _environ={"TERM": "xterm"},
        color_system="standard",
        width=120,
    )
    renderer = SoperatorUpgradeProgress(console, terminal=True)

    with renderer.sequence() as sequence:
        sequence.start("maintenance-entry", "Entering durable Slurm maintenance")
        progress = sequence._progress  # noqa: SLF001
        assert progress is not None

        def _raise_render_error() -> None:
            raise OSError("terminal stream closed")

        monkeypatch.setattr(progress, failure_method, _raise_render_error)
        completed = False
        with sequence.paused():
            completed = True

        assert completed is True
        assert sequence._disabled is True  # noqa: SLF001


def test_non_terminal_progress_is_stable_ansi_free_stderr_style_output() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, no_color=True, width=120)
    renderer = SoperatorUpgradeProgress(console, terminal=False)

    with renderer.phase(
        "release-freeze",
        "Resolving official release",
        success="Official release verified",
    ):
        pass

    assert output.getvalue().splitlines() == [
        "Soperator upgrade: START Resolving official release",
        "Soperator upgrade: OK Official release verified",
    ]
    assert "\x1b" not in output.getvalue()


def test_non_terminal_milestones_are_visible_bounded_and_fail_closed() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, no_color=True, width=120)
    renderer = SoperatorUpgradeProgress(console, terminal=False)

    with (
        pytest.raises(RuntimeError, match="writer remains active"),
        renderer.phase(
            "operation-authority",
            "Acquiring exclusive campaign authority",
        ) as sequence,
    ):
        with sequence.paused():
            sequence.milestone("Expired operation Lease reclaimed; waiting for prior writers")
        sequence.milestone(
            "Verifying the Slurm maintenance barrier (pass 1)",
            key="maintenance-barrier",
        )
        sequence.milestone(
            "Verifying the Slurm maintenance barrier (pass 2)",
            key="maintenance-barrier",
        )
        raise RuntimeError("writer remains active")

    assert output.getvalue().splitlines() == [
        "Soperator upgrade: START Acquiring exclusive campaign authority",
        "Soperator upgrade: INFO Expired operation Lease reclaimed; waiting for prior writers",
        "Soperator upgrade: INFO Verifying the Slurm maintenance barrier (pass 1)",
        "Soperator upgrade: FAILED Verifying the Slurm maintenance barrier (pass 2)",
    ]
    assert " OK " not in output.getvalue()
    assert "\x1b" not in output.getvalue()


def test_progress_failure_and_interrupt_never_render_success() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, no_color=True, width=120)
    renderer = SoperatorUpgradeProgress(console, terminal=False)

    with pytest.raises(KeyboardInterrupt), renderer.phase("flux", "Migrating Flux APIs"):
        raise KeyboardInterrupt

    assert output.getvalue().splitlines() == [
        "Soperator upgrade: START Migrating Flux APIs",
        "Soperator upgrade: FAILED Migrating Flux APIs",
    ]
    assert " OK " not in output.getvalue()


def test_progress_rendering_failure_does_not_change_operational_control_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = Console(file=StringIO(), force_terminal=False)

    def _raise_render_error(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("terminal stream closed")

    monkeypatch.setattr(console, "print", _raise_render_error)
    renderer = SoperatorUpgradeProgress(console, terminal=False)
    completed = False

    with renderer.phase("provider", "Reading provider inventory"):
        completed = True

    assert completed is True


def test_rendered_flux_progress_keeps_live_updates_off_result_stdout() -> None:
    stdout = StringIO()
    stderr = StringIO()
    result_console = Console(file=stdout, force_terminal=False, no_color=True)
    progress_console = Console(
        file=stderr,
        force_terminal=True,
        _environ={"TERM": "xterm"},
        color_system="standard",
        width=120,
    )
    renderer = SoperatorUpgradeProgress(progress_console, terminal=True)

    with rendered_flux_progress_surface(
        result_console,
        renderer,
        terminal=True,
    ) as (start_phase, update_detail, _sink):
        start_phase("[cyan]Waiting for Soperator readiness...[/cyan]")
        update_detail("worker-0 pending\nworker-1 ready")
    result_console.print("Soperator release reconcile receipt: receipt.json")

    assert stdout.getvalue() == "Soperator release reconcile receipt: receipt.json\n"
    assert "Waiting for Soperator readiness" in stderr.getvalue()
    assert "worker-0 pending worker-1 ready" in stderr.getvalue()
