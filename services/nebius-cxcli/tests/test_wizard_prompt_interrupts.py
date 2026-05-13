from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest
import yaml

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


def test_prompt_choice_override_tty_renders_only_selectable_values(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, object] = {}

    class _FakePrompt:
        def ask(self):
            return "cpu-e2"

    def _fake_select(*args, **kwargs):
        captured["choices"] = kwargs.get("choices")
        captured["instruction"] = kwargs.get("instruction")
        return _FakePrompt()

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        select=_fake_select,
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)

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
    assert value == "cpu-e2"
    titles = [choice["title"] for choice in captured["choices"]]
    assert titles == ["cpu-d3", "cpu-e2"]
    assert "<manual input>" not in titles
    assert captured["instruction"] == "Use arrows; q=back; qq=quit; Enter=select."


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


def test_prompt_choice_override_text_prompt_rejects_unlisted_value(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    responses = iter(["cpu-z9", "2"])
    printed: list[str] = []

    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(cli.console, "print", lambda message, **_kwargs: printed.append(str(message)))

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
    assert value == "cpu-e2"
    assert any("Invalid option value" in item for item in printed)


def test_mk8s_gpu_stack_source_static_choices_resolve_without_provider_lookup() -> None:
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="Managed Kubernetes",
        wizard_fields={
            "inputs.gpu_stack_source": {
                "sources": [
                    {
                        "source": "static",
                        "values": ["nebius_image", "operator_managed"],
                    }
                ]
            }
        },
    )

    choices = cli._resolve_dynamic_field_choices(
        payload={},
        entry=entry,
        full_path_label="infra.components[0].inputs.gpu_stack_source",
        provider_lookup=None,
    )

    assert [choice.value for choice in choices] == ["nebius_image", "operator_managed"]


def test_static_wizard_choices_can_carry_operator_facing_labels() -> None:
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="Managed Kubernetes",
        wizard_fields={
            "inputs.gpu_stack_source": {
                "sources": [
                    {
                        "source": "static",
                        "values": [
                            {
                                "value": "nebius_image",
                                "label": (
                                    "nebius_image  (Nebius GPU image includes the host "
                                    "NVIDIA driver/toolkit; GPU Operator does not "
                                    "install them)"
                                ),
                            },
                            {
                                "value": "operator_managed",
                                "label": (
                                    "operator_managed  (base OS image; GPU Operator "
                                    "installs and manages the NVIDIA driver/toolkit)"
                                ),
                            },
                        ],
                    }
                ]
            }
        },
    )

    choices = cli._resolve_dynamic_field_choices(
        payload={},
        entry=entry,
        full_path_label="infra.components[0].inputs.gpu_stack_source",
        provider_lookup=None,
    )

    assert [choice.value for choice in choices] == ["nebius_image", "operator_managed"]
    assert choices[0].label.startswith("nebius_image  (Nebius GPU image includes")
    assert choices[1].label.startswith("operator_managed  (base OS image")


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


def test_prompt_scalar_override_parses_mysterybox_secret_list(monkeypatch) -> None:
    secret_list = [
        {
            "name": "app-runtime",
            "version_id": "n/a",
            "payload": {"API_KEY": {"type": "text"}},
        }
    ]
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: json.dumps(secret_list))

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.secrets",
        [],
        type_hint="list(object({ name = string }))",
        required=True,
    )

    assert should_stop is False
    assert value == secret_list


def test_prompt_scalar_override_guides_mysterybox_secret_payload_pairs(monkeypatch) -> None:
    responses = iter(
        [
            "db-uname-pass",
            "",
            "",
            "username",
            "",
            "password",
            "text",
            "",
            "",
        ]
    )
    prompts: list[str] = []

    def _prompt(message: str, **_kwargs):
        prompts.append(message)
        return next(responses)

    monkeypatch.setattr(cli.typer, "prompt", _prompt)
    captured: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda message: captured.append(str(message)))

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.secrets",
        [],
        type_hint='list(object({ name = string payload = map(object({ type = optional(string, "text") })) }))',
        required=True,
    )

    assert should_stop is False
    assert value == [
        {
            "name": "db-uname-pass",
            "version_id": "n/a",
            "eso_version_policy": "auto-primary-version-pinning",
            "kubernetes_secret_name": "db-uname-pass",
            "payload": {
                "USERNAME": {"type": "text"},
                "PASSWORD": {"type": "text"},
            },
        }
    ]
    assert prompts[0] == "MysteryBox Secret name (required, q=back, qq=quit wizard)"
    assert prompts[1] == (
        "Kubernetes Secret name for db-uname-pass (q=back, qq=quit wizard)"
    )
    assert prompts[2] == (
        "ESO version policy for db-uname-pass [required] "
        "(enter q to go back; qq quits wizard) (index or value)"
    )
    assert prompts[3] == "Payload key for db-uname-pass (required, q=back, qq=quit wizard)"
    assert prompts[4] == (
        "Payload type for USERNAME [required] "
        "(enter q to go back; qq quits wizard) (index or value)"
    )
    assert (
        prompts[5]
        == "Payload key for db-uname-pass (blank=finish Secret, q=back, qq=quit wizard)"
    )
    assert prompts[-1] == "MysteryBox Secret name (blank=done, q=back, qq=quit wizard)"
    assert any("Entered USERNAME as the key." in item for item in captured)
    assert any("Entered PASSWORD as the key." in item for item in captured)
    assert any(
        "Added MysteryBox Secret db-uname-pass with 2 payload key(s), syncing to Kubernetes Secret db-uname-pass."
        in item
        for item in captured
    )


def test_prompt_scalar_override_accepts_custom_mysterybox_kubernetes_secret_name(
    monkeypatch,
) -> None:
    responses = iter(["db-uname-pass", "app-db-creds", "", "username", "", "", ""])

    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.secrets",
        [],
        type_hint='list(object({ name = string payload = map(object({ type = optional(string, "text") })) }))',
        required=True,
    )

    assert should_stop is False
    assert value == [
        {
            "name": "db-uname-pass",
            "version_id": "n/a",
            "eso_version_policy": "auto-primary-version-pinning",
            "kubernetes_secret_name": "app-db-creds",
            "payload": {
                "USERNAME": {"type": "text"},
            },
        }
    ]


def test_prompt_scalar_override_defaults_mysterybox_kubernetes_secret_name_to_dns_label(
    monkeypatch,
) -> None:
    responses = iter(["db_credentials", "", "", "password", "", "", ""])
    defaults: list[object] = []

    def _prompt(_message: str, **kwargs):
        defaults.append(kwargs.get("default"))
        return next(responses)

    monkeypatch.setattr(cli.typer, "prompt", _prompt)
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.secrets",
        [],
        type_hint='list(object({ name = string payload = map(object({ type = optional(string, "text") })) }))',
        required=True,
    )

    assert should_stop is False
    assert defaults[1] == "db-credentials"
    assert value == [
        {
            "name": "db_credentials",
            "version_id": "n/a",
            "eso_version_policy": "auto-primary-version-pinning",
            "kubernetes_secret_name": "db-credentials",
            "payload": {
                "PASSWORD": {"type": "text"},
            },
        }
    ]


def test_mysterybox_eso_version_policy_uses_tty_select(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, object] = {}

    class _FakePrompt:
        def ask(self):
            return "manual-version-pinning"

    def _fake_select(*args, **kwargs):
        captured["message"] = args[0]
        captured["choices"] = kwargs.get("choices")
        return _FakePrompt()

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        select=_fake_select,
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)

    value, should_stop = cli._prompt_mysterybox_eso_version_policy("db_credentials")

    assert should_stop is False
    assert value == "manual-version-pinning"
    assert captured["message"] == "ESO version policy for db_credentials [required]"
    titles = [choice["title"] for choice in captured["choices"]]
    assert titles == ["auto-primary-version-pinning", "manual-version-pinning"]


def test_mysterybox_payload_type_uses_tty_select(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, object] = {}

    class _FakePrompt:
        def ask(self):
            return "file"

    def _fake_select(*args, **kwargs):
        captured["message"] = args[0]
        captured["choices"] = kwargs.get("choices")
        return _FakePrompt()

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        select=_fake_select,
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)

    value, should_stop = cli._prompt_mysterybox_payload_type("PASSWORD")

    assert should_stop is False
    assert value == "file"
    assert captured["message"] == "Payload type for PASSWORD [required]"
    titles = [choice["title"] for choice in captured["choices"]]
    assert titles == ["text", "file"]


def test_prompt_scalar_override_accepts_manual_mysterybox_eso_version_policy(
    monkeypatch,
) -> None:
    responses = iter(
        [
            "app-config",
            "",
            "manual-version-pinning",
            "db_password",
            "",
            "",
            "",
        ]
    )

    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.secrets",
        [],
        type_hint='list(object({ name = string payload = map(object({ type = optional(string, "text") })) }))',
        required=True,
    )

    assert should_stop is False
    assert value == [
        {
            "name": "app-config",
            "version_id": "n/a",
            "eso_version_policy": "manual-version-pinning",
            "kubernetes_secret_name": "app-config",
            "payload": {
                "DB_PASSWORD": {"type": "text"},
            },
        }
    ]


def test_mysterybox_guided_prompt_q_at_first_payload_key_returns_to_secret_name(
    monkeypatch,
) -> None:
    responses = iter(
        [
            "db-username-password",
            "",
            "",
            "username",
            "",
            "password",
            "",
            "",
            "apikey",
            "",
            "",
            "q",
            "api-key-fixed",
            "",
            "",
            "apikey",
            "",
            "",
            "",
        ]
    )
    prompts: list[str] = []

    def _prompt(message: str, **_kwargs):
        prompts.append(message)
        return next(responses)

    monkeypatch.setattr(cli.typer, "prompt", _prompt)
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.secrets",
        [],
        type_hint='list(object({ name = string payload = map(object({ type = optional(string, "text") })) }))',
        required=True,
    )

    assert should_stop is False
    assert value == [
        {
            "name": "db-username-password",
            "version_id": "n/a",
            "eso_version_policy": "auto-primary-version-pinning",
            "kubernetes_secret_name": "db-username-password",
            "payload": {
                "USERNAME": {"type": "text"},
                "PASSWORD": {"type": "text"},
            },
        },
        {
            "name": "api-key-fixed",
            "version_id": "n/a",
            "eso_version_policy": "auto-primary-version-pinning",
            "kubernetes_secret_name": "api-key-fixed",
            "payload": {
                "APIKEY": {"type": "text"},
            },
        },
    ]
    apikey_prompt_index = prompts.index(
        "Payload key for apikey (required, q=back, qq=quit wizard)"
    )
    assert prompts[apikey_prompt_index + 1] == (
        "MysteryBox Secret name (blank=done, q=back, qq=quit wizard)"
    )


def test_mysterybox_guided_prompt_q_at_payload_type_returns_to_payload_key(
    monkeypatch,
) -> None:
    responses = iter(["runtime", "", "", "token", "q", "api_token", "", "", ""])
    prompts: list[str] = []

    def _prompt(message: str, **_kwargs):
        prompts.append(message)
        return next(responses)

    monkeypatch.setattr(cli.typer, "prompt", _prompt)
    monkeypatch.setattr(cli.console, "print", lambda *_args, **_kwargs: None)

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.secrets",
        [],
        type_hint='list(object({ name = string payload = map(object({ type = optional(string, "text") })) }))',
        required=True,
    )

    assert should_stop is False
    assert value == [
        {
            "name": "runtime",
            "version_id": "n/a",
            "eso_version_policy": "auto-primary-version-pinning",
            "kubernetes_secret_name": "runtime",
            "payload": {
                "API_TOKEN": {"type": "text"},
            },
        }
    ]
    assert prompts.count("Payload key for runtime (required, q=back, qq=quit wizard)") == 2


def test_prompt_scalar_override_parses_yaml_list(monkeypatch) -> None:
    monkeypatch.setattr(
        cli.typer,
        "prompt",
        lambda *_args, **_kwargs: (
            '[{"name":"app-runtime","version_id":"n/a","payload":{"API_KEY":{"type":"text"}}}]'
        ),
    )

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.secrets",
        {},
        type_hint="list(object({}))",
        required=True,
    )

    assert should_stop is False
    assert value == [
        {
            "name": "app-runtime",
            "version_id": "n/a",
            "payload": {
                "API_KEY": {
                    "type": "text",
                },
            },
        },
    ]


def test_prompt_component_with_checkboxes_tty_cancel_quits_wizard(monkeypatch) -> None:
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

    with pytest.raises(cli._WizardQuitRequested):
        cli._prompt_component_with_checkboxes(
            scope="infra",
            entries=entries,
            defaults=set(),
        )


def test_questionary_wizard_navigation_registers_q_and_qq_keys() -> None:
    class _FakeKeyBindings:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, ...], bool]] = []

        def add(self, *keys: str, eager: bool = False):
            self.calls.append((keys, eager))

            def _decorator(fn):
                return fn

            return _decorator

    class _FakePrompt:
        def __init__(self) -> None:
            self.application = SimpleNamespace(key_bindings=_FakeKeyBindings())

        def ask(self):
            return "selected"

    prompt = _FakePrompt()

    assert cli._ask_questionary_with_wizard_navigation(prompt) == "selected"
    assert (("q", "q"), True) in prompt.application.key_bindings.calls
    assert (("q",), False) in prompt.application.key_bindings.calls


def test_prompt_component_with_checkboxes_tty_uses_key_navigation_without_control_rows(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, object] = {}

    class _FakePrompt:
        def ask(self):
            return ["mk8s"]

    def _fake_checkbox(*_args, **kwargs):
        captured["choices"] = kwargs["choices"]
        captured["instruction"] = kwargs["instruction"]
        return _FakePrompt()

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        checkbox=_fake_checkbox,
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

    selected = cli._prompt_component_with_checkboxes(
        scope="infra",
        entries=entries,
        defaults={"mk8s"},
    )

    titles = [choice["title"] for choice in captured["choices"]]
    assert selected == ["mk8s"]
    assert titles == ["mk8s  (mk8s)"]
    assert "< Back" not in titles
    assert "< Quit wizard" not in titles
    assert "q=back; qq=quit" in str(captured["instruction"])


def test_resolve_component_ids_prints_interactive_selection_summary(monkeypatch) -> None:
    rendered_messages: list[str] = []
    monkeypatch.setattr(cli, "_prompt_component_with_checkboxes", lambda **_kwargs: ["mk8s"])
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda *args, **_kwargs: rendered_messages.append(" ".join(str(arg) for arg in args)),
    )
    entries = (
        ComponentEntry(
            id="mk8s",
            scope="infra",
            config_path="infra.mk8s",
            description="mk8s",
        ),
    )

    selected = cli._resolve_component_ids(
        scope="infra",
        raw_values=None,
        interactive=True,
        entries=entries,
    )

    assert selected == {"mk8s"}
    assert any("Selected infra components: mk8s" in message for message in rendered_messages)


def test_wizard_continue_phase_q_backs_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "q")

    decision = cli._wizard_continue_phase("Configure component?", allow_back=True)

    assert decision.back is True
    assert decision.quit is False


def test_wizard_continue_phase_qq_quits(monkeypatch) -> None:
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "qq")

    decision = cli._wizard_continue_phase("Configure component?", allow_back=True)

    assert decision.quit is True
    assert cli._wizard_phase_stop_requested(decision) is True


def test_prompt_component_with_checkboxes_text_q_backs(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "q")
    entries = (
        ComponentEntry(
            id="mk8s",
            scope="infra",
            config_path="infra.mk8s",
            description="mk8s",
        ),
    )

    with pytest.raises(cli._WizardBackRequested):
        cli._prompt_component_with_checkboxes(scope="infra", entries=entries, defaults=set())


def test_component_field_wizard_q_at_first_field_returns_to_component_phase(monkeypatch) -> None:
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "demo",
                    "instance_id": "demo",
                    "enabled": True,
                    "inputs": {"first": "one"},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="demo",
        scope="infra",
        config_path="infra.components[].inputs",
        description="demo",
        wizard_fields={"inputs.first": {}},
    )
    decisions = [
        cli._WizardPhaseDecision(proceed=True),
        cli._WizardPhaseDecision(proceed=False),
    ]

    monkeypatch.setattr(cli, "_wizard_continue_phase", lambda *_args, **_kwargs: decisions.pop(0))
    monkeypatch.setattr(
        cli,
        "_prompt_scalar_override",
        lambda *_args, **_kwargs: (cli._WIZARD_BACKTRACK, False),
    )
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "n")

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"demo"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    assert completed is True
    assert updated_payload["infra"]["components"][0]["inputs"]["first"] == "one"
    assert decisions == []


def test_component_field_wizard_q_revisits_previous_field(monkeypatch) -> None:
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "demo",
                    "instance_id": "demo",
                    "enabled": True,
                    "inputs": {"first": "one", "second": "two"},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="demo",
        scope="infra",
        config_path="infra.components[].inputs",
        description="demo",
        wizard_fields={"inputs.first": {}, "inputs.second": {}},
    )
    answers = {
        "infra.components[0].inputs.first": ["one-updated", "one-final"],
        "infra.components[0].inputs.second": [cli._WIZARD_BACKTRACK, "two-final"],
    }

    def _answer(path_label: str, current, **_kwargs):
        pending = answers[path_label]
        answer = pending.pop(0)
        return answer, False

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli, "_prompt_scalar_override", _answer)

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"demo"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    assert completed is True
    assert updated_payload["infra"]["components"][0]["inputs"]["first"] == "one-final"
    assert updated_payload["infra"]["components"][0]["inputs"]["second"] == "two-final"


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
            OptionChoice(
                value="us-central1-a",
                label="us-central1-a  (gpu-h200-sxm, us-central1, recommended)",
                recommended=True,
            ),
        ],
        type_hint="string",
        required=False,
    )

    assert should_stop is False
    assert value == ""
    assert captured["default"] == "us-central1-a"
    assert "blank keeps unset" in str(captured["text"])


def test_prompt_choice_override_tty_keeps_skip_for_optional_recommended_default(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, object] = {}

    class _FakePrompt:
        def ask(self):
            return "__skip__"

    def _fake_select(*args, **kwargs):
        captured["default"] = kwargs.get("default")
        captured["choices"] = kwargs.get("choices")
        captured["instruction"] = kwargs.get("instruction")
        return _FakePrompt()

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        select=_fake_select,
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.infiniband_fabric",
        current="",
        choices=[
            OptionChoice(
                value="fabric-2",
                label="fabric-2  (gpu-h100-sxm, eu-north1, recommended)",
                recommended=True,
            ),
            OptionChoice(
                value="fabric-3",
                label="fabric-3  (gpu-h100-sxm, eu-north1)",
            ),
        ],
        type_hint="string",
        required=False,
    )

    assert should_stop is False
    assert value == ""
    assert captured["default"] == "fabric-2"
    titles = [choice["title"] for choice in captured["choices"]]
    assert titles[0] == "<skip / keep unset>"
    assert "<manual input>" not in titles
    assert "< Back" not in titles
    assert "< Quit wizard" not in titles
    assert "q=back; qq=quit" in str(captured["instruction"])


def test_maybe_print_gpu_preset_prompt_guidance_for_gpu_shape(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda message: captured.append(str(message)))

    cli._maybe_print_gpu_preset_prompt_guidance(
        payload={
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "gpu_nodes_platform": "gpu-h100-sxm",
                        }
                    }
                ]
            }
        },
        entry=ComponentEntry(
            id="mk8s",
            scope="infra",
            config_path="infra.mk8s",
            description="mk8s",
        ),
        full_path_label="infra.components[0].inputs.gpu_nodes_preset",
        emitted_guidance=set(),
    )

    assert any("Ethernet-only" in message for message in captured)


def test_maybe_print_selected_gpu_preset_guidance_for_single_gpu_shape(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda message: captured.append(str(message)))

    provider_lookup = SimpleNamespace(
        compute_platform_preset_resources=lambda **_kwargs: (16, 200, 1),
        compute_platform_preset_allows_gpu_clustering=lambda **_kwargs: False,
    )

    cli._maybe_print_selected_gpu_preset_guidance(
        payload={
            "client_info": {
                "nebius": {
                    "project_id": "project-123",
                }
            },
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "gpu_nodes_platform": "gpu-h100-sxm",
                            "gpu_nodes_preset": "1gpu-16vcpu-200gb",
                        }
                    }
                ]
            },
        },
        entry=ComponentEntry(
            id="mk8s",
            scope="infra",
            config_path="infra.mk8s",
            description="mk8s",
        ),
        full_path_label="infra.components[0].inputs.gpu_nodes_preset",
        provider_lookup=provider_lookup,
        emitted_guidance=set(),
    )

    assert any("not production distributed training" in message for message in captured)


def test_maybe_print_selected_gpu_preset_guidance_for_vm_single_gpu_shape(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda message: captured.append(str(message)))

    provider_lookup = SimpleNamespace(
        compute_platform_preset_resources=lambda **_kwargs: (16, 200, 1),
        compute_platform_preset_allows_gpu_clustering=lambda **_kwargs: False,
    )

    cli._maybe_print_selected_gpu_preset_guidance(
        payload={
            "client_info": {
                "nebius": {
                    "project_id": "project-123",
                }
            },
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "platform": "gpu-h100-sxm",
                            "preset": "1gpu-16vcpu-200gb",
                        }
                    }
                ]
            },
        },
        entry=ComponentEntry(
            id="vm",
            scope="infra",
            config_path="infra.vm",
            description="vm",
        ),
        full_path_label="infra.components[0].inputs.preset",
        provider_lookup=provider_lookup,
        emitted_guidance=set(),
    )

    assert any("not production distributed training" in message for message in captured)


def test_maybe_print_mysterybox_secrets_prompt_guidance_marks_values_runtime_only(
    monkeypatch,
) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda *args, **_kwargs: captured.append(" ".join(str(arg) for arg in args)),
    )
    entry = ComponentEntry(
        id="mysterybox",
        scope="infra",
        config_path="infra.components.mysterybox",
        description="MysteryBox",
    )
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mysterybox",
                    "instance_id": "secretstore-alpha",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        }
    }
    emitted: set[str] = set()

    cli._maybe_print_mysterybox_secrets_prompt_guidance(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.secrets",
        emitted_guidance=emitted,
    )
    cli._maybe_print_mysterybox_secrets_prompt_guidance(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.secrets",
        emitted_guidance=emitted,
    )

    assert len(captured) == 1
    assert "enter Secret names and payload keys only" in captured[0]
    assert "TF_VAR_secretstore_alpha_payload_values" in captured[0]


def test_maybe_clear_gpu_cluster_fabric_after_shape_change_for_vm(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda message: captured.append(str(message)))
    monkeypatch.setattr(cli, "_resolve_dynamic_field_choices", lambda **_kwargs: [])

    provider_lookup = SimpleNamespace(last_error=lambda: None)
    payload = {
        "infra": {
            "components": [
                {
                    "inputs": {
                        "gpu_cluster_enabled": True,
                        "platform": "gpu-h100-sxm",
                        "preset": "1gpu-16vcpu-200gb",
                        "gpu_cluster_infiniband_fabric": "fabric-2",
                    }
                }
            ]
        }
    }

    cli._maybe_clear_gpu_cluster_fabric_after_shape_change(
        payload=payload,
        entry=ComponentEntry(
            id="vm",
            scope="infra",
            config_path="infra.vm",
            description="vm",
        ),
        full_path_label="infra.components[0].inputs.preset",
        provider_lookup=provider_lookup,
    )

    assert "gpu_cluster_infiniband_fabric" not in payload["infra"]["components"][0]["inputs"]
    assert any("gpu_cluster_infiniband_fabric" in message for message in captured)


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


def test_prompt_scalar_override_accepts_comma_list_for_string_sequences(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_prompt(text: str, default=None):
        captured["text"] = text
        captured["default"] = default
        return "ns1, ns2"

    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)

    value, should_stop = cli._prompt_scalar_override(
        "deploy.targets[0].secrets.mysterybox.sync_namespaces",
        [],
        type_hint="list(string)",
        required=False,
    )

    assert should_stop is False
    assert value == ["ns1", "ns2"]
    assert captured["default"] == ""
    assert "enter a comma-separated list" in str(captured["text"])
    assert "enter a single-line YAML/JSON value" not in str(captured["text"])


def test_prompt_scalar_override_accepts_comma_list_for_string_sets(monkeypatch) -> None:
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "zone-a,zone-b")

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.zones",
        [],
        type_hint="set(string)",
        required=False,
    )

    assert should_stop is False
    assert value == ["zone-a", "zone-b"]


def test_prompt_scalar_override_reprompts_invalid_string_sequence(monkeypatch) -> None:
    prompts = iter(["ns1,,ns2", "ns1,ns2"])
    messages: list[str] = []
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: next(prompts))
    monkeypatch.setattr(cli.console, "print", lambda message: messages.append(str(message)))

    value, should_stop = cli._prompt_scalar_override(
        "deploy.targets[0].secrets.mysterybox.sync_namespaces",
        [],
        type_hint="list(string)",
        required=False,
    )

    assert should_stop is False
    assert value == ["ns1", "ns2"]
    assert any("Expected a comma-separated list of strings." in message for message in messages)


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
