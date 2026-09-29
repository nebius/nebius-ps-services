"""Shared terminal presentation for severity and copyable commands."""

from __future__ import annotations

import re

from rich.console import Console
from rich.markup import escape
from rich.text import Text

WARNING_COLOR = "#ffbf00"
ERROR_COLOR = "red"
COPY_PASTE_COMMAND_COLOR = "#202020"
COPY_PASTE_COMMAND_BACKGROUND = "#e5e7eb"
COPY_PASTE_COMMAND_STYLE = f"bold {COPY_PASTE_COMMAND_COLOR} on {COPY_PASTE_COMMAND_BACKGROUND}"
HELP_EXAMPLE_SEPARATOR_COLOR = "#00d7ff"


def _styled_markup(text: str, *, color: str, bold: bool = False) -> str:
    style = f"bold {color}" if bold else color
    return f"[{style}]{escape(text)}[/]"


def warning_markup(text: str, *, bold: bool = False) -> str:
    return _styled_markup(text, color=WARNING_COLOR, bold=bold)


def error_markup(text: str, *, bold: bool = False) -> str:
    return _styled_markup(text, color=ERROR_COLOR, bold=bold)


def copy_paste_command_markup(command: str) -> str:
    return f"[{COPY_PASTE_COMMAND_STYLE}]{escape(command)}[/]"


def print_copy_paste_command(console: Console, command: str) -> None:
    normalized = command.strip()
    if normalized:
        console.print(
            Text(normalized, style=COPY_PASTE_COMMAND_STYLE), highlight=False, soft_wrap=True
        )


def highlight_copy_paste_examples(body: str, *, prefix: str) -> str:
    """Style only command lines already identified by the help normalizer."""
    return re.sub(
        rf"(?m)^{re.escape(prefix)}(nebius-cxcli [^\n]+)$",
        lambda match: prefix + copy_paste_command_markup(match[1]),
        body,
    )


__all__ = [
    "COPY_PASTE_COMMAND_COLOR",
    "COPY_PASTE_COMMAND_BACKGROUND",
    "COPY_PASTE_COMMAND_STYLE",
    "HELP_EXAMPLE_SEPARATOR_COLOR",
    "ERROR_COLOR",
    "WARNING_COLOR",
    "copy_paste_command_markup",
    "highlight_copy_paste_examples",
    "print_copy_paste_command",
    "error_markup",
    "warning_markup",
]
