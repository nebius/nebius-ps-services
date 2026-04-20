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
    monkeypatch.setattr(
        cli.typer, "prompt", lambda *_args, **_kwargs: (_ for _ in ()).throw(cli.typer.Abort())
    )

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.parent_id",
        current="project-123",
        choices=[OptionChoice(value="project-123", label="project-123")],
    )

    assert value == "project-123"
    assert should_stop is True


def test_prompt_choice_override_text_prompt_q_backtracks_one_level(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "q")

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.parent_id",
        current="project-123",
        choices=[OptionChoice(value="project-123", label="project-123")],
    )

    assert value is cli._WIZARD_BACKTRACK
    assert should_stop is False


def test_prompt_choice_override_text_prompt_qq_stops_wizard(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "qq")

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.parent_id",
        current="project-123",
        choices=[OptionChoice(value="project-123", label="project-123")],
    )

    assert value == "project-123"
    assert should_stop is True


def test_prompt_scalar_override_abort_stops_wizard(monkeypatch) -> None:
    monkeypatch.setattr(
        cli.typer, "prompt", lambda *_args, **_kwargs: (_ for _ in ()).throw(cli.typer.Abort())
    )

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.cluster_name",
        "cluster-a",
    )

    assert value == "cluster-a"
    assert should_stop is True


def test_prompt_scalar_override_q_backtracks_one_level(monkeypatch) -> None:
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "q")

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.cluster_name",
        "cluster-a",
    )

    assert value is cli._WIZARD_BACKTRACK
    assert should_stop is False


def test_prompt_scalar_override_qq_stops_wizard(monkeypatch) -> None:
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "qq")

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.cluster_name",
        "cluster-a",
    )

    assert value == "cluster-a"
    assert should_stop is True


def test_prompt_scalar_override_parses_yaml_list(monkeypatch) -> None:
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: '["203.0.113.10/32"]')

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.allowed_cidrs",
        [],
        type_hint="list(string)",
        required=True,
    )

    assert should_stop is False
    assert value == ["203.0.113.10/32"]


def test_prompt_scalar_override_parses_yaml_mapping(monkeypatch) -> None:
    monkeypatch.setattr(
        cli.typer,
        "prompt",
        lambda *_args, **_kwargs: '{"app":{"name":"app-runtime","payload_keys":["API_KEY"]}}',
    )

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.secrets",
        {},
        type_hint="map(object({}))",
        required=True,
    )

    assert should_stop is False
    assert value == {"app": {"name": "app-runtime", "payload_keys": ["API_KEY"]}}


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


def test_prompt_choice_override_defaults_to_first_option_when_required(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "")

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.cpu_nodes_platform",
        current="",
        choices=[
            OptionChoice(value="cpu-d3", label="cpu-d3"),
            OptionChoice(value="cpu-e2", label="cpu-e2"),
        ],
        required=True,
    )

    assert should_stop is False
    assert value == "cpu-d3"


def test_prompt_choice_override_keeps_optional_field_unset_when_empty(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "")

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.k8s_version",
        current=None,
        choices=[
            OptionChoice(value="1.31", label="1.31"),
            OptionChoice(value="1.32", label="1.32"),
        ],
        required=False,
    )

    assert should_stop is False
    assert value is None


def test_prompt_choice_override_optional_empty_prompt_mentions_blank_keeps_unset(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    captured: dict[str, object] = {}

    def _fake_prompt(text: str, default=None):
        captured["text"] = text
        captured["default"] = default
        return ""

    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.infiniband_fabric",
        current="",
        choices=[
            OptionChoice(value="us-central1-a", label="us-central1-a  (gpu-h200-sxm, us-central1)"),
        ],
        type_hint="string",
        required=False,
    )

    assert should_stop is False
    assert value == ""
    assert captured["default"] == ""
    assert "blank keeps unset" in str(captured["text"])


def test_prompt_scalar_override_reprompts_blank_for_required_field(monkeypatch) -> None:
    prompts = iter(["", "cluster-a"])
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: next(prompts))

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.cluster_name",
        None,
        type_hint="string",
        required=True,
    )

    assert should_stop is False
    assert value == "cluster-a"


def test_prompt_scalar_override_uses_blank_default_for_empty_optional_map(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_prompt(text: str, default=None):
        captured["text"] = text
        captured["default"] = default
        return ""

    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.gpu_nodes_os",
        {},
        type_hint="map(string)",
        required=False,
    )

    assert should_stop is False
    assert value == {}
    assert captured["default"] == ""
    assert "enter a single-line YAML/JSON value" in str(captured["text"])
    assert "blank keeps current empty map {}" in str(captured["text"])


def test_provider_fallback_warning_for_optional_field_mentions_blank_is_allowed() -> None:
    warning = cli._provider_fallback_warning(
        field_path_label="infra.components[0].inputs.gpu_nodes_os",
        provider_names="compute_platform_presets",
        required=False,
        provider_lookup=None,
    )

    assert "Dynamic provider options unavailable" in warning
    assert "The next prompt is manual input only." in warning
    assert (
        "Press Enter there to keep the current value or leave the optional field unset." in warning
    )


def test_provider_fallback_warning_for_required_field_mentions_manual_entry_is_required() -> None:
    warning = cli._provider_fallback_warning(
        field_path_label="infra.components[0].inputs.network_id",
        provider_names="project_networks",
        required=True,
        provider_lookup=None,
    )

    assert "Dynamic provider options unavailable" in warning
    assert "The next prompt is manual input only" in warning
    assert "must be entered manually" in warning
