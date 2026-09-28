"""Interactive acceptance must own the terminal without a competing live display."""

from io import StringIO

import pytest
from rich.console import Console

from nebius_cxcli.soperator_acceptance import AcceptanceControl
from nebius_cxcli.soperator_upgrade_progress import (
    SoperatorUpgradeProgress,
    rendered_flux_progress_surface,
)


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("outcome", [True, False, EOFError, KeyboardInterrupt])
def test_acceptance_prompt_pauses_then_restores_live_progress(monkeypatch, structured, outcome):
    output = StringIO()
    console = Console(file=output, force_terminal=True, color_system=None)
    progress = SoperatorUpgradeProgress(console) if structured else None
    control = AcceptanceControl(requested="ask")

    def confirm(*args, **kwargs):
        assert kwargs == {"default": False}
        assert not console._live_stack, "progress must stop before interactive input"
        if isinstance(outcome, type):
            raise outcome()
        return outcome

    monkeypatch.setattr("typer.confirm", confirm)
    with rendered_flux_progress_surface(console, progress, terminal=True) as (phase, _, _):
        phase("Waiting for acceptance")
        assert console._live_stack
        if isinstance(outcome, type):
            with pytest.raises(outcome):
                control.choose()
            assert control.chosen is None
        else:
            assert control.choose() == ("full" if outcome else "readiness")
        assert len(console._live_stack) == 1
    assert not console._live_stack
