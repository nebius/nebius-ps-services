from __future__ import annotations

from nebius_cxcli.terminal_styles import (
    COPY_PASTE_COMMAND_COLOR,
    ERROR_COLOR,
    WARNING_COLOR,
    copy_paste_command_markup,
    error_markup,
    warning_markup,
)


def test_warning_markup_uses_amber() -> None:
    assert warning_markup("Warning") == f"[{WARNING_COLOR}]Warning[/]"
    assert warning_markup("Warning", bold=True) == f"[bold {WARNING_COLOR}]Warning[/]"


def test_error_markup_uses_red() -> None:
    assert error_markup("Error") == f"[{ERROR_COLOR}]Error[/]"
    assert error_markup("Error", bold=True) == f"[bold {ERROR_COLOR}]Error[/]"


def test_copy_paste_command_markup_uses_bold_cyan_and_escapes_markup() -> None:
    command = "nebius-cxcli render /tmp/[red]project[/red]/config.yaml"

    assert copy_paste_command_markup(command) == (
        f"[bold {COPY_PASTE_COMMAND_COLOR}]"
        "nebius-cxcli render /tmp/\\[red]project\\[/red]/config.yaml"
        "[/]"
    )
