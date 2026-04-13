"""Shared Rich markup helpers for CLI severity styling."""

from __future__ import annotations

from rich.markup import escape

WARNING_COLOR = "#ffbf00"
ERROR_COLOR = "red"


def _styled_markup(text: str, *, color: str, bold: bool = False) -> str:
    style = f"bold {color}" if bold else color
    return f"[{style}]{escape(text)}[/]"


def warning_markup(text: str, *, bold: bool = False) -> str:
    return _styled_markup(text, color=WARNING_COLOR, bold=bold)


def error_markup(text: str, *, bold: bool = False) -> str:
    return _styled_markup(text, color=ERROR_COLOR, bold=bold)


__all__ = ["ERROR_COLOR", "WARNING_COLOR", "error_markup", "warning_markup"]
