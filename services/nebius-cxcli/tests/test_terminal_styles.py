from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console
from rich.text import Text

from nebius_cxcli.terminal_styles import (
    COPY_PASTE_COMMAND_STYLE,
    ERROR_COLOR,
    WARNING_COLOR,
    copy_paste_command_markup,
    error_markup,
    highlight_copy_paste_examples,
    print_copy_paste_command,
    warning_markup,
)


def test_warning_markup_uses_amber() -> None:
    assert warning_markup("Warning") == f"[{WARNING_COLOR}]Warning[/]"
    assert warning_markup("Warning", bold=True) == f"[bold {WARNING_COLOR}]Warning[/]"


def test_error_markup_uses_red() -> None:
    assert error_markup("Error") == f"[{ERROR_COLOR}]Error[/]"
    assert error_markup("Error", bold=True) == f"[bold {ERROR_COLOR}]Error[/]"


def test_copy_paste_command_markup_uses_shared_background_and_escapes_markup() -> None:
    command = "nebius-cxcli render /tmp/[red]project[/red]/config.yaml"

    assert copy_paste_command_markup(command) == (
        f"[{COPY_PASTE_COMMAND_STYLE}]"
        "nebius-cxcli render /tmp/\\[red]project\\[/red]/config.yaml"
        "[/]"
    )


@pytest.mark.parametrize("color_system", ["truecolor", "256", "standard"])
def test_command_highlight_preserves_long_literal_text_and_resets_style(monkeypatch, color_system):
    monkeypatch.delenv("NO_COLOR", raising=False)
    output = StringIO()
    console = Console(file=output, force_terminal=True, color_system=color_system, width=20)
    command = (
        "kubectl --kubeconfig '/tmp/[red]project path/config' get secret admin "
        "-o 'jsonpath={.data.admin-password}' | base64 --decode && printf '\\n'"
    )
    print_copy_paste_command(console, command)
    console.print("Following label", highlight=False)
    text = Text.from_ansi(output.getvalue())
    assert text.plain == command + "\nFollowing label"
    assert output.getvalue().endswith("\n")
    for offset in range(len(command)):
        style = text.get_style_at_offset(console, offset)
        assert style.bold and style.bgcolor is not None and style.color is not None
        assert sum(style.color.get_truecolor()) < sum(style.bgcolor.get_truecolor())
    assert text.get_style_at_offset(console, len(command) + 1).bgcolor is None


@pytest.mark.parametrize("terminal,no_color", [(False, False), (True, True)])
def test_command_output_respects_terminal_and_no_color(terminal, no_color):
    output = StringIO()
    console = Console(file=output, force_terminal=terminal, no_color=no_color, width=20)
    command = "nebius-cxcli render '/tmp/project path/config.yaml'"
    print_copy_paste_command(console, command)
    rendered = output.getvalue()
    text = Text.from_ansi(rendered)
    assert text.plain == command
    assert rendered.endswith("\n")
    assert all(span.style.color is None and span.style.bgcolor is None for span in text.spans)
    if not terminal:
        assert rendered == command + "\n"


def test_help_highlighting_excludes_labels_and_separators():
    prefix = "[bold cyan]|[/] "
    command = "nebius-cxcli render '/tmp/[red]project[/red]/config.yaml'"
    text = Text.from_markup(
        highlight_copy_paste_examples(
            f"Render next:\n\n{prefix}{command}\n\nComments: keep files.", prefix=prefix
        )
    )
    assert text.plain == f"Render next:\n\n| {command}\n\nComments: keep files."
    console = Console()
    start = text.plain.index(command)
    assert text.get_style_at_offset(console, start).bgcolor is not None
    assert text.get_style_at_offset(console, 0).bgcolor is None
    assert text.get_style_at_offset(console, start - 2).bgcolor is None
