from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import nebius_cxcli.cli as cli
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.provider_options import OptionChoice


def test_prompt_choice_override_tty_cancel_stops_wizard(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)

    class _FakePrompt:
        def ask(self):
            return None

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        select=lambda *args, **kwargs: _FakePrompt(),
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.parent_id",
        current="project-123",
        choices=[OptionChoice(value="project-123", label="project-123")],
    )

    assert value == "project-123"
    assert should_stop is True


def test_prompt_choice_override_text_prompt_abort_stops_wizard(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: (_ for _ in ()).throw(cli.typer.Abort()))

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.parent_id",
        current="project-123",
        choices=[OptionChoice(value="project-123", label="project-123")],
    )

    assert value == "project-123"
    assert should_stop is True


def test_prompt_scalar_override_abort_stops_wizard(monkeypatch) -> None:
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: (_ for _ in ()).throw(cli.typer.Abort()))

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.cluster_name",
        "cluster-a",
    )

    assert value == "cluster-a"
    assert should_stop is True


def test_prompt_component_with_checkboxes_tty_cancel_raises_abort(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)

    class _FakePrompt:
        def ask(self):
            return None

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        checkbox=lambda *args, **kwargs: _FakePrompt(),
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)
    entries = (
        ComponentEntry(
            id="mk8s",
            scope="infra",
            config_path="infra.mk8s",
            description="mk8s",
        ),
    )

    with pytest.raises(cli.typer.Abort):
        cli._prompt_component_with_checkboxes(
            scope="infra",
            entries=entries,
            defaults=set(),
        )


def test_prompt_choice_override_defaults_to_first_option_when_empty(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "")

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.cpu_nodes_platform",
        current="",
        choices=[
            OptionChoice(value="cpu-d3", label="cpu-d3"),
            OptionChoice(value="cpu-e2", label="cpu-e2"),
        ],
    )

    assert should_stop is False
    assert value == "cpu-d3"
