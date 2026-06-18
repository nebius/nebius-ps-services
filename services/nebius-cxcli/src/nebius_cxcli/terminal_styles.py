"""Shared Rich markup helpers for CLI severity styling."""

from __future__ import annotations

from rich.markup import escape

WARNING_COLOR = "#ffbf00"
ERROR_COLOR = "red"
COPY_PASTE_COMMAND_COLOR = "#00d7ff"


def _styled_markup(text: str, *, color: str, bold: bool = False) -> str:
    style = f"bold {color}" if bold else color
    return f"[{style}]{escape(text)}[/]"


def warning_markup(text: str, *, bold: bool = False) -> str:
    return _styled_markup(text, color=WARNING_COLOR, bold=bold)


def error_markup(text: str, *, bold: bool = False) -> str:
    return _styled_markup(text, color=ERROR_COLOR, bold=bold)


def copy_paste_command_markup(command: str) -> str:
    return _styled_markup(command, color=COPY_PASTE_COMMAND_COLOR, bold=True)


__all__ = [
    "COPY_PASTE_COMMAND_COLOR",
    "ERROR_COLOR",
    "WARNING_COLOR",
    "copy_paste_command_markup",
    "error_markup",
    "warning_markup",
]
