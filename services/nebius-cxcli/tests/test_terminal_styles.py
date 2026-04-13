from __future__ import annotations

from nebius_cxcli.terminal_styles import ERROR_COLOR, WARNING_COLOR, error_markup, warning_markup


def test_warning_markup_uses_amber() -> None:
    assert warning_markup("Warning") == f"[{WARNING_COLOR}]Warning[/]"
    assert warning_markup("Warning", bold=True) == f"[bold {WARNING_COLOR}]Warning[/]"


def test_error_markup_uses_red() -> None:
    assert error_markup("Error") == f"[{ERROR_COLOR}]Error[/]"
    assert error_markup("Error", bold=True) == f"[bold {ERROR_COLOR}]Error[/]"
