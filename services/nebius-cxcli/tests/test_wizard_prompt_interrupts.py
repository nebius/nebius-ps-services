from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import nebius_cxcli.cli as cli
from nebius_cxcli.capacity_dashboard import CapacityAdviceAvailability, CapacityResourceAdvice
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.provider_options import OptionChoice, ProviderOptionLookup
from nebius_cxcli.wizard_profiles import BUILTIN_WIZARD_PROFILES

_VALID_ED25519_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f demo@example"
)
_EXPECTED_VPC_CUSTOM_PRIVATE_CIDR_SUGGESTIONS = (
    "10.8.0.0/13",
    "10.16.0.0/13",
    "10.32.0.0/13",
    "10.40.0.0/13",
    "10.56.0.0/13",
    "172.16.0.0/12",
    "192.168.0.0/16",
)


def test_upgrade_discovery_status_uses_spinner_for_terminal(monkeypatch) -> None:
    events: list[object] = []
    message = "[cyan]Discovering live MK8s state and preparing node-template upgrade plan...[/cyan]"

    class _FakeStatus:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            events.append("exit")
            return False

    class _FakeConsole:
        is_terminal = True

        def status(self, status_message: str, *, spinner: str):
            events.append(("status", status_message, spinner))
            return _FakeStatus()

        def print(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("terminal status should use console.status")

    monkeypatch.setattr(cli, "console", _FakeConsole())

    with cli._upgrade_discovery_status(message):
        events.append("body")

    assert events == [
        ("status", message, "dots"),
        "enter",
        "body",
        "exit",
    ]


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
    captured: dict[str, Any] = {}

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


def test_vpc_existing_network_tty_skip_choice_creates_new_network(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, Any] = {}

    class _FakePrompt:
        def ask(self):
            return "__skip__"

    def _fake_select(*args, **kwargs):
        captured["choices"] = kwargs.get("choices")
        captured["default"] = kwargs.get("default")
        return _FakePrompt()

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        select=_fake_select,
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.network.existing_id",
        current=None,
        choices=[
            OptionChoice(
                value="vpcnetwork-live",
                label="default-network",
                recommended=True,
            )
        ],
        required=False,
        unset_on_skip=True,
    )

    assert should_stop is False
    assert value is None
    titles = [choice["title"] for choice in captured["choices"]]
    assert titles[0] == "Create a new VPC network"
    assert titles[1] == "default-network"
    assert captured["default"] == "vpcnetwork-live"


def test_vpc_private_pool_tty_skip_choice_creates_pool_from_cidr(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, Any] = {}

    class _FakePrompt:
        def ask(self):
            return "__skip__"

    def _fake_select(*args, **kwargs):
        captured["choices"] = kwargs.get("choices")
        return _FakePrompt()

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        select=_fake_select,
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.network.ipv4_private_pool_ids",
        current=None,
        choices=[OptionChoice(value="vpcpool-private", label="default private pool")],
        required=False,
        unset_on_skip=True,
    )

    assert should_stop is False
    assert value is None
    titles = [choice["title"] for choice in captured["choices"]]
    assert titles[0] == "Create a new private pool from CIDR"
    assert titles[1] == "default private pool"


def test_ssh_public_key_prompt_lists_local_pub_files(tmp_path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    ssh_dir = home_dir / ".ssh"
    ssh_dir.mkdir(parents=True)
    key_path = ssh_dir / "id_ed25519.pub"
    key_path.write_text(_VALID_ED25519_PUBLIC_KEY + "\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "1")

    choices = cli._ssh_public_key_file_choices()
    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.ssh_public_key",
        "",
        required=True,
    )

    assert should_stop is False
    assert value == _VALID_ED25519_PUBLIC_KEY
    assert choices[0].label.startswith("~/.ssh/id_ed25519.pub")


def test_ssh_public_key_prompt_accepts_manual_pub_path(tmp_path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    (home_dir / ".ssh").mkdir(parents=True)
    key_path = tmp_path / "my_ssh_key.pub"
    key_path.write_text(_VALID_ED25519_PUBLIC_KEY + "\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: str(key_path))

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.ssh_public_key",
        "",
        required=True,
    )

    assert should_stop is False
    assert value == _VALID_ED25519_PUBLIC_KEY


def test_ssh_public_key_prompt_keeps_current_unmatched_inline_key(tmp_path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    ssh_dir = home_dir / ".ssh"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "id_ed25519.pub").write_text(_VALID_ED25519_PUBLIC_KEY + "\n", encoding="utf-8")
    current_key = f"{_VALID_ED25519_PUBLIC_KEY} current"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)

    def _fake_prompt(*_args, **kwargs):
        assert kwargs.get("default") == ""
        return ""

    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.ssh_public_key",
        current_key,
        required=True,
    )

    assert should_stop is False
    assert value == current_key


def test_ssh_public_key_prompt_tty_includes_manual_choice(tmp_path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    ssh_dir = home_dir / ".ssh"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "my_ssh_key.pub").write_text(_VALID_ED25519_PUBLIC_KEY + "\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, Any] = {}

    class _FakePrompt:
        def ask(self):
            return _VALID_ED25519_PUBLIC_KEY

    def _fake_select(*args, **kwargs):
        captured["choices"] = kwargs.get("choices")
        return _FakePrompt()

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        select=_fake_select,
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.ssh_public_key",
        "",
        required=True,
    )

    assert should_stop is False
    assert value == _VALID_ED25519_PUBLIC_KEY
    titles = [choice["title"] for choice in captured["choices"]]
    assert titles[0].startswith("~/.ssh/my_ssh_key.pub")
    assert titles[-1] == "<manual path or inline public key>"


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
    monkeypatch.setattr(
        cli.console, "print", lambda message, **_kwargs: printed.append(str(message))
    )

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


def test_prompt_scalar_override_q_backtracks_from_integer_default(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_prompt(text: str, default=None):
        captured["text"] = text
        captured["default"] = default
        return "q"

    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.soperator.worker_gpu_nodes_per_group",
        100,
        type_hint="number",
        required=False,
    )

    assert value is cli._WIZARD_BACKTRACK
    assert should_stop is False
    assert captured["default"] == "100"
    assert "enter q to go back" in str(captured["text"])


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
    assert prompts[1] == ("Kubernetes Secret name for db-uname-pass (q=back, qq=quit wizard)")
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
        prompts[5] == "Payload key for db-uname-pass (blank=finish Secret, q=back, qq=quit wizard)"
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
    captured: dict[str, Any] = {}

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
    captured: dict[str, Any] = {}

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
    apikey_prompt_index = prompts.index("Payload key for apikey (required, q=back, qq=quit wizard)")
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
    captured: dict[str, Any] = {}

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


def test_component_field_wizard_guides_vpc_subnets_without_raw_yaml_prompt(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    prompt_texts: list[str] = []
    answers = {
        "infra.components[0].inputs.network.name": "workloads-network",
        "infra.components[0].inputs.network.ipv4_private_cidrs": "10.10.0.0/16",
        "infra.components[0].inputs.subnets.<new>.name": "workloads",
        "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs": "10.10.0.0/24",
        "infra.components[0].inputs.subnets.add_another": "false",
    }

    class _EmptyProviderLookup(ProviderOptionLookup):
        def resolve(self, **_kwargs):
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        prompt_texts.append(text)
        for path_label, answer in answers.items():
            if path_label in text:
                return answer
        return "" if default is None else str(default)

    def _phase_decision(prompt_label: str, **_kwargs):
        assert not prompt_label.startswith("Extend the selected live VPC network")
        return cli._WizardPhaseDecision(proceed=True)

    monkeypatch.setattr(cli, "_wizard_continue_phase", _phase_decision)
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_EmptyProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is True
    assert inputs["network"] == {
        "name": "workloads-network",
        "ipv4_private_cidrs": ["10.10.0.0/16"],
    }
    assert inputs["subnets"] == {
        "workloads": {
            "name": "workloads",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["10.10.0.0/24"],
        }
    }
    assert any("inputs.subnets.<new>.name" in text for text in prompt_texts)
    assert any("select a suggested custom CIDR" in text for text in prompt_texts)
    assert all("enter a single-line YAML/JSON value" not in text for text in prompt_texts)


def test_component_field_wizard_allows_vpc_network_without_subnets_when_live_networks_exist(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    prompt_texts: list[str] = []
    answers = {
        "infra.components[0].inputs.network.existing_id": "",
        "infra.components[0].inputs.network.name": "mynetwork",
        "infra.components[0].inputs.network.ipv4_private_cidrs": "1",
        "infra.components[0].inputs.subnets.add": "false",
    }
    provider_calls: list[str] = []

    class _ProviderLookup(ProviderOptionLookup):
        def resolve(self, **kwargs):
            field_path = str(kwargs.get("field_path", ""))
            provider_calls.append(field_path)
            if field_path.endswith(".inputs.network.existing_id"):
                return [OptionChoice(value="vpcnetwork-live", label="default network")]
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        prompt_texts.append(text)
        for path_label, answer in answers.items():
            if path_label in text:
                return answer
        if "infra.components[0].inputs.subnets.<new>.name" in text:
            raise AssertionError("subnet name should not be prompted when subnets are skipped")
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_ProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is True
    assert inputs["network"] == {
        "name": "mynetwork",
        "ipv4_private_cidrs": ["10.8.0.0/13"],
    }
    assert "existing_id" not in inputs["network"]
    assert "subnets" not in inputs
    assert any("inputs.network.existing_id" in path for path in provider_calls)
    assert any("inputs.subnets.add" in text for text in prompt_texts)
    assert all("inputs.subnets.<new>.name" not in text for text in prompt_texts)
    assert all("enter a single-line YAML/JSON value" not in text for text in prompt_texts)


def test_component_field_wizard_existing_vpc_network_skips_network_name_and_retries_cidr(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    prompt_texts: list[str] = []
    captured: list[str] = []
    cidr_answers = ["not-a-cidr", "10.1.0.0/16"]

    class _ProviderLookup(ProviderOptionLookup):
        def resolve(self, **kwargs):
            if str(kwargs.get("field_path", "")).endswith(".inputs.network.existing_id"):
                return [
                    OptionChoice(
                        value="vpcnetwork-live",
                        label="default network",
                        metadata={"private_cidrs": ("10.0.0.0/13",)},
                    )
                ]
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        prompt_texts.append(text)
        if "infra.components[0].inputs.network.existing_id" in text:
            return "1"
        if "infra.components[0].inputs.network.name" in text:
            raise AssertionError("network.name should not be prompted for an existing network")
        if "infra.components[0].inputs.network.ipv4_private_cidrs" in text:
            raise AssertionError(
                "network private CIDRs should not be prompted for an existing network"
            )
        if "infra.components[0].inputs.subnets.<new>.name" in text:
            return "workloads"
        if "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs" in text:
            return cidr_answers.pop(0)
        if "infra.components[0].inputs.subnets.add_another" in text:
            return "false"
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)
    monkeypatch.setattr(
        cli.console, "print", lambda message="", **_kwargs: captured.append(str(message))
    )

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_ProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is True
    assert inputs["network"] == {"existing_id": "vpcnetwork-live"}
    assert inputs["subnets"] == {
        "workloads": {
            "name": "workloads",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["10.1.0.0/16"],
        }
    }
    assert cidr_answers == []
    assert sum("inputs.subnets.workloads.ipv4_private_cidrs" in text for text in prompt_texts) == 2
    assert "'not-a-cidr' is not a valid IPv4 CIDR" in "\n".join(captured)
    assert all("enter a single-line YAML/JSON value" not in text for text in prompt_texts)


def test_component_field_wizard_accepts_region_vpc_cidr_suggestion(
    monkeypatch,
) -> None:
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "eu-west1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    prompt_texts: list[str] = []
    answers = {
        "infra.components[0].inputs.network.name": "workloads-network",
        "infra.components[0].inputs.network.ipv4_private_cidrs": "",
        "infra.components[0].inputs.subnets.<new>.name": "workloads",
        "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs": "1",
        "infra.components[0].inputs.subnets.add_another": "false",
    }

    class _EmptyProviderLookup(ProviderOptionLookup):
        def resolve(self, **_kwargs):
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        prompt_texts.append(text)
        for path_label, answer in answers.items():
            if path_label in text:
                return answer
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_EmptyProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    subnet = inputs["subnets"]["workloads"]
    assert completed is True
    assert inputs["network"]["ipv4_private_cidrs"] == ["10.8.0.0/13"]
    assert subnet == {
        "name": "workloads",
        "use_network_private_pools": False,
        "ipv4_private_cidrs": ["10.8.0.0/16"],
    }
    suggestions = cli._vpc_custom_private_cidr_suggestions(region_id="eu-west1")
    assert suggestions == _EXPECTED_VPC_CUSTOM_PRIVATE_CIDR_SUGGESTIONS
    assert all(cli._vpc_default_private_pool_overlap(cidr) is None for cidr in suggestions)
    assert any("select a suggested custom CIDR" in text for text in prompt_texts)


def test_vpc_subnet_cidr_prompt_choices_suggest_child_ranges_inside_parent_pool() -> None:
    choices = cli._vpc_subnet_cidr_prompt_choices(
        region_id="eu-north1",
        existing_cidrs=("10.0.0.0/16",),
        parent_cidrs=("10.0.0.0/13",),
    )

    assert [(choice.value, choice.recommended) for choice in choices] == [
        ("10.1.0.0/16", True),
        ("10.2.0.0/16", False),
        ("10.3.0.0/16", False),
        ("10.4.0.0/16", False),
    ]
    assert all(
        choice.metadata
        == {
            "suggestion_kind": "subnet_child",
            "parent_cidr": "10.0.0.0/13",
        }
        for choice in choices
    )


def test_vpc_subnet_cidr_prompt_choices_skip_ranges_with_private_allocations() -> None:
    choices = cli._vpc_subnet_cidr_prompt_choices(
        region_id="eu-north1",
        existing_cidrs=("10.0.0.42/32",),
        parent_cidrs=("10.0.0.0/13",),
    )

    assert [choice.value for choice in choices] == [
        "10.1.0.0/16",
        "10.2.0.0/16",
        "10.3.0.0/16",
        "10.4.0.0/16",
    ]


def test_vpc_subnet_cidr_prompt_choices_include_parent_extension_for_new_network() -> None:
    choices = cli._vpc_subnet_cidr_prompt_choices(
        region_id="eu-north1",
        existing_cidrs=(),
        parent_cidrs=("172.16.0.0/12",),
        allow_parent_extension=True,
    )

    assert [choice.value for choice in choices] == [
        "172.16.0.0/16",
        "172.17.0.0/16",
        "172.18.0.0/16",
        "172.19.0.0/16",
        "192.168.0.0/16",
    ]
    assert choices[-1].metadata == {"suggestion_kind": "parent_extension"}
    assert "extends Terraform-owned network before subnet" in choices[-1].label


def test_vpc_subnet_cidr_prompt_choices_combine_existing_children_and_extensions() -> None:
    choices = cli._vpc_subnet_cidr_prompt_choices(
        region_id="eu-north1",
        existing_cidrs=("10.0.0.0/16",),
        parent_cidrs=("10.0.0.0/13",),
        allow_parent_extension=True,
        parent_extension_description="extends selected live network attached private pool",
        allow_custom_fallback=False,
    )

    assert [choice.value for choice in choices] == [
        "10.1.0.0/16",
        "10.2.0.0/16",
        "10.3.0.0/16",
        "10.4.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
    ]
    assert [choice.metadata["suggestion_kind"] for choice in choices] == [
        "subnet_child",
        "subnet_child",
        "subnet_child",
        "subnet_child",
        "parent_extension",
        "parent_extension",
    ]
    assert choices[-2].label == (
        "172.16.0.0/12  (new parent private block; extends selected live network "
        "attached private pool)"
    )
    assert choices[-1].label == (
        "192.168.0.0/16  (192.168 parent private block; extends selected live network "
        "attached private pool)"
    )


def test_vpc_subnet_cidr_prompt_choices_keep_attached_rfc1918_parent_blocks_visible() -> None:
    choices = cli._vpc_subnet_cidr_prompt_choices(
        region_id="eu-north1",
        existing_cidrs=("10.0.0.0/16", "10.2.0.0/16"),
        parent_cidrs=("10.0.0.0/13", "172.16.0.0/12", "192.168.0.0/16"),
        allow_parent_extension=True,
        parent_extension_description="extends selected live network attached private pool",
        suggest_whole_parent_cidrs=True,
        allow_custom_fallback=False,
    )

    assert [choice.value for choice in choices] == [
        "10.1.0.0/16",
        "10.3.0.0/16",
        "10.4.0.0/16",
        "10.5.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
    ]
    assert [choice.metadata["suggestion_kind"] for choice in choices] == [
        "subnet_child",
        "subnet_child",
        "subnet_child",
        "subnet_child",
        "subnet_child",
        "subnet_child",
    ]
    assert choices[-2].metadata["parent_cidr"] == "172.16.0.0/12"
    assert choices[-2].label == ("172.16.0.0/12  (subnet child range inside 172.16.0.0/12)")
    assert choices[-1].metadata["parent_cidr"] == "192.168.0.0/16"
    assert choices[-1].label == ("192.168.0.0/16  (subnet child range inside 192.168.0.0/16)")


def test_vpc_subnet_cidr_prompt_choices_skip_whole_parent_block_with_allocations() -> None:
    choices = cli._vpc_subnet_cidr_prompt_choices(
        region_id="eu-north1",
        existing_cidrs=("172.16.30.0/24",),
        parent_cidrs=("172.16.0.0/12",),
        allow_parent_extension=True,
        parent_extension_description="extends selected live network attached private pool",
        suggest_whole_parent_cidrs=True,
        allow_custom_fallback=False,
    )

    assert "172.16.0.0/12" not in [choice.value for choice in choices]
    assert "172.17.0.0/16" in [choice.value for choice in choices]


def test_vpc_private_cidr_tty_custom_rejects_comma_separated_values(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, Any] = {}
    messages: list[str] = []
    answers = ["172.16.30.0/24, 172.16.20.0/24", "172.16.30.0/24"]

    class _FakePrompt:
        def ask(self):
            return "__custom__"

    def _fake_select(*_args, **kwargs):
        captured["choices"] = kwargs["choices"]
        captured["instruction"] = kwargs["instruction"]
        return _FakePrompt()

    def _fake_prompt(text: str, default=None, **_kwargs):
        captured["text"] = text
        captured["default"] = default
        return answers.pop(0)

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        select=_fake_select,
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)
    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)
    monkeypatch.setattr(
        cli.console, "print", lambda message="", **_kwargs: messages.append(str(message))
    )

    value, should_stop = cli._prompt_vpc_private_cidr_override(
        path_label="infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs",
        current=[],
        choices=[
            OptionChoice(
                value="10.1.0.0/16",
                label="10.1.0.0/16  (subnet child range inside 10.0.0.0/13)",
            )
        ],
        region_id="eu-north1",
        type_hint="list(string)",
        allow_multiple=False,
    )

    assert should_stop is False
    assert value == ["172.16.30.0/24"]
    assert answers == []
    titles = [choice["title"] for choice in captured["choices"]]
    assert titles[-1] == "Enter custom CIDR"
    assert "q=back; qq=quit" in str(captured["instruction"])
    assert "enter one CIDR" in str(captured["text"])
    assert captured["default"] == ""
    assert "multi-CIDR mode" in "\n".join(messages)


def _patch_vpc_extension_sdk_bindings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    network_client: type,
    pool_client: type,
) -> None:
    class _Message:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _MaskedRequest(_Message):
        def set_mask(self, mask):
            self._mask = mask

        def get_mask(self):
            return self._mask

    class _Mask:
        def __init__(self, paths: list[str]):
            self._paths = paths

        @classmethod
        def unmarshal(cls, value: str):
            return cls([value])

        def marshal(self):
            return list(self._paths)

    monkeypatch.setattr(
        cli,
        "_load_vpc_extension_sdk_bindings",
        lambda: {
            "AddressBlockState": SimpleNamespace(AVAILABLE="AVAILABLE"),
            "ResourceMetadata": _Message,
            "GetNetworkRequest": _Message,
            "GetPoolRequest": _Message,
            "IpVersion": SimpleNamespace(IPV4="IPV4"),
            "IpVisibility": SimpleNamespace(PRIVATE="PRIVATE"),
            "NetworkServiceClient": network_client,
            "PoolCidr": _Message,
            "PoolServiceClient": pool_client,
            "PoolSpec": _Message,
            "UpdatePoolRequest": _MaskedRequest,
            "Mask": _Mask,
        },
    )


def test_extend_existing_vpc_parent_private_cidrs_updates_attached_pool_cidrs(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    sync_waits: list[int | None] = []

    class _RequestResult:
        def __init__(self, value):
            self._value = value

        def wait(self):
            return self._value

    class _Operation:
        def __init__(self, resource_id: str = ""):
            self.resource_id = resource_id

        def sync_wait(self, timeout=None):
            sync_waits.append(timeout)

    class _Sdk:
        def sync_close(self):
            captured["sdk_closed"] = True

    network = SimpleNamespace(
        metadata=SimpleNamespace(
            id="vpcnetwork-live",
            parent_id="project-1",
            name="default-network",
        ),
        spec=SimpleNamespace(
            ipv4_private_pools=SimpleNamespace(pools=[SimpleNamespace(id="vpcpool-default")]),
        ),
    )
    default_pool = SimpleNamespace(
        metadata=SimpleNamespace(
            id="vpcpool-default",
            parent_id="project-1",
            name="default-network-pool",
            resource_version=17,
        ),
        spec=SimpleNamespace(
            version="IPV4",
            visibility="PRIVATE",
            cidrs=[SimpleNamespace(cidr="10.0.0.0/13", state="AVAILABLE", max_mask_length=32)],
        ),
        status=SimpleNamespace(cidrs=["10.0.0.0/13"]),
    )

    class _NetworkClient:
        def __init__(self, sdk):
            captured["network_sdk"] = sdk

        def get(self, request):
            captured["get_network_id"] = request.id
            return _RequestResult(network)

    class _PoolClient:
        def __init__(self, sdk):
            captured["pool_sdk"] = sdk

        def get(self, request):
            captured.setdefault("get_pool_ids", []).append(request.id)
            return _RequestResult(default_pool)

        def update(self, request):
            captured["update_pool_request"] = request
            return _RequestResult(_Operation())

    sdk = _Sdk()
    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: sdk)
    _patch_vpc_extension_sdk_bindings(
        monkeypatch,
        network_client=_NetworkClient,
        pool_client=_PoolClient,
    )

    result = cli._extend_existing_vpc_parent_private_cidrs(
        project_id="project-1",
        network_id="vpcnetwork-live",
        cidrs=("172.16.10.0/24",),
    )

    assert result == ("10.0.0.0/13", "172.16.10.0/24")
    assert captured["get_network_id"] == "vpcnetwork-live"
    assert captured["get_pool_ids"] == ["vpcpool-default"]
    update_request = captured["update_pool_request"]
    update_mask = update_request.get_mask().marshal()
    assert update_mask == ["spec.cidrs"]
    assert update_request.metadata.id == "vpcpool-default"
    assert update_request.metadata.parent_id == "project-1"
    assert update_request.metadata.name == "default-network-pool"
    assert update_request.metadata.resource_version == 17
    assert update_request.spec.version == "IPV4"
    assert update_request.spec.visibility == "PRIVATE"
    assert [entry.cidr for entry in update_request.spec.cidrs] == [
        "10.0.0.0/13",
        "172.16.10.0/24",
    ]
    assert update_request.spec.cidrs[-1].state == "AVAILABLE"
    assert sync_waits == [120]
    assert captured["sdk_closed"] is True


def test_extend_existing_vpc_parent_private_cidrs_updates_once_for_multiple_missing_cidrs(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    sync_waits: list[int | None] = []

    class _RequestResult:
        def __init__(self, value):
            self._value = value

        def wait(self):
            return self._value

    class _Operation:
        def sync_wait(self, timeout=None):
            sync_waits.append(timeout)

    class _Sdk:
        def sync_close(self):
            captured["sdk_closed"] = True

    network = SimpleNamespace(
        metadata=SimpleNamespace(id="vpcnetwork-live", parent_id="project-1"),
        spec=SimpleNamespace(
            ipv4_private_pools=SimpleNamespace(pools=[SimpleNamespace(id="vpcpool-default")]),
        ),
    )
    default_pool = SimpleNamespace(
        metadata=SimpleNamespace(id="vpcpool-default", name="default-network-pool"),
        spec=SimpleNamespace(version="IPV4", visibility="PRIVATE", cidrs=["10.0.0.0/13"]),
        status=SimpleNamespace(cidrs=["10.0.0.0/13"]),
    )

    class _NetworkClient:
        def __init__(self, _sdk):
            pass

        def get(self, request):
            captured["get_network_id"] = request.id
            return _RequestResult(network)

    class _PoolClient:
        def __init__(self, _sdk):
            pass

        def get(self, request):
            captured.setdefault("get_pool_ids", []).append(request.id)
            return _RequestResult(default_pool)

        def update(self, request):
            captured["update_pool_request"] = request
            return _RequestResult(_Operation())

    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: _Sdk())
    _patch_vpc_extension_sdk_bindings(
        monkeypatch,
        network_client=_NetworkClient,
        pool_client=_PoolClient,
    )

    result = cli._extend_existing_vpc_parent_private_cidrs(
        project_id="project-1",
        network_id="vpcnetwork-live",
        cidrs=("172.16.10.0/24", "192.168.0.0/16"),
    )

    assert result == ("10.0.0.0/13", "172.16.10.0/24", "192.168.0.0/16")
    assert captured["get_network_id"] == "vpcnetwork-live"
    update_request = captured["update_pool_request"]
    assert update_request.metadata.id == "vpcpool-default"
    assert [entry.cidr for entry in update_request.spec.cidrs] == [
        "10.0.0.0/13",
        "172.16.10.0/24",
        "192.168.0.0/16",
    ]
    assert sync_waits == [120]
    assert captured["sdk_closed"] is True


def test_extend_existing_vpc_parent_private_cidrs_rejects_overlapping_parent(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    class _RequestResult:
        def __init__(self, value):
            self._value = value

        def wait(self):
            return self._value

    class _Sdk:
        def sync_close(self):
            captured["sdk_closed"] = True

    network = SimpleNamespace(
        metadata=SimpleNamespace(id="vpcnetwork-live", parent_id="project-1"),
        spec=SimpleNamespace(
            ipv4_private_pools=SimpleNamespace(pools=[SimpleNamespace(id="vpcpool-default")]),
        ),
    )
    default_pool = SimpleNamespace(
        metadata=SimpleNamespace(id="vpcpool-default"),
        spec=SimpleNamespace(version="IPV4", visibility="PRIVATE", cidrs=["10.0.0.0/13"]),
        status=SimpleNamespace(cidrs=["10.0.0.0/13"]),
    )

    class _NetworkClient:
        def __init__(self, _sdk):
            pass

        def get(self, request):
            captured["get_network_id"] = request.id
            return _RequestResult(network)

    class _PoolClient:
        def __init__(self, _sdk):
            pass

        def get(self, request):
            captured.setdefault("get_pool_ids", []).append(request.id)
            return _RequestResult(default_pool)

        def update(self, _request):
            raise AssertionError("overlapping parent extension should not update the pool")

    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: _Sdk())
    _patch_vpc_extension_sdk_bindings(
        monkeypatch,
        network_client=_NetworkClient,
        pool_client=_PoolClient,
    )

    with pytest.raises(RuntimeError, match="overlaps existing VPC network private CIDR"):
        cli._extend_existing_vpc_parent_private_cidrs(
            project_id="project-1",
            network_id="vpcnetwork-live",
            cidrs=("10.0.0.0/12",),
        )

    assert captured["get_pool_ids"] == ["vpcpool-default"]
    assert captured["sdk_closed"] is True


def test_extend_existing_vpc_parent_private_cidrs_fails_without_attached_private_pool(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    class _RequestResult:
        def __init__(self, value):
            self._value = value

        def wait(self):
            return self._value

    class _Sdk:
        def sync_close(self):
            captured["sdk_closed"] = True

    network = SimpleNamespace(
        metadata=SimpleNamespace(id="vpcnetwork-live", parent_id="project-1"),
        spec=SimpleNamespace(ipv4_private_pools=SimpleNamespace(pools=[])),
    )

    class _NetworkClient:
        def __init__(self, _sdk):
            pass

        def get(self, request):
            captured["get_network_id"] = request.id
            return _RequestResult(network)

    class _PoolClient:
        def __init__(self, _sdk):
            pass

        def get(self, request):
            captured.setdefault("get_pool_ids", []).append(request.id)
            raise AssertionError("network has no private pools to inspect")

    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: _Sdk())
    _patch_vpc_extension_sdk_bindings(
        monkeypatch,
        network_client=_NetworkClient,
        pool_client=_PoolClient,
    )

    with pytest.raises(RuntimeError, match="has no attached private pools"):
        cli._extend_existing_vpc_parent_private_cidrs(
            project_id="project-1",
            network_id="vpcnetwork-live",
            cidrs=("172.16.10.0/24",),
        )

    assert captured["get_network_id"] == "vpcnetwork-live"
    assert "get_pool_ids" not in captured
    assert captured["sdk_closed"] is True


def test_extend_existing_vpc_parent_private_cidrs_fails_when_attached_pool_unreadable(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    class _RequestResult:
        def __init__(self, value):
            self._value = value

        def wait(self):
            return self._value

    class _Sdk:
        def sync_close(self):
            captured["sdk_closed"] = True

    network = SimpleNamespace(
        metadata=SimpleNamespace(
            id="vpcnetwork-live",
            parent_id="project-1",
            name="default-network",
            resource_version=12,
        ),
        spec=SimpleNamespace(
            ipv4_private_pools=SimpleNamespace(pools=[SimpleNamespace(id="vpcpool-default")]),
        ),
    )

    class _NetworkClient:
        def __init__(self, _sdk):
            pass

        def get(self, request):
            captured["get_network_id"] = request.id
            return _RequestResult(network)

    class _PoolClient:
        def __init__(self, _sdk):
            pass

        def get(self, request):
            captured.setdefault("get_pool_ids", []).append(request.id)
            raise RuntimeError("pool API unavailable")

    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: _Sdk())
    _patch_vpc_extension_sdk_bindings(
        monkeypatch,
        network_client=_NetworkClient,
        pool_client=_PoolClient,
    )

    with pytest.raises(RuntimeError, match="Could not inspect attached VPC private pool"):
        cli._extend_existing_vpc_parent_private_cidrs(
            project_id="project-1",
            network_id="vpcnetwork-live",
            cidrs=("10.0.1.0/24",),
        )

    assert captured["get_pool_ids"] == ["vpcpool-default"]
    assert captured["sdk_closed"] is True


def test_extend_existing_vpc_parent_private_cidrs_noops_when_pool_already_attached(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    class _RequestResult:
        def __init__(self, value):
            self._value = value

        def wait(self):
            return self._value

    class _Sdk:
        def sync_close(self):
            captured["sdk_closed"] = True

    network = SimpleNamespace(
        metadata=SimpleNamespace(
            id="vpcnetwork-live",
            parent_id="project-1",
            name="default-network",
            resource_version=9,
        ),
        spec=SimpleNamespace(
            ipv4_private_pools=SimpleNamespace(
                pools=[
                    SimpleNamespace(id="vpcpool-default"),
                    SimpleNamespace(id="vpcpool-existing"),
                ]
            ),
        ),
    )
    pools = {
        "vpcpool-default": SimpleNamespace(
            metadata=SimpleNamespace(id="vpcpool-default"),
            spec=SimpleNamespace(version="IPV4", visibility="PRIVATE", cidrs=["10.0.0.0/13"]),
            status=SimpleNamespace(cidrs=["10.0.0.0/13"]),
        ),
        "vpcpool-existing": SimpleNamespace(
            metadata=SimpleNamespace(id="vpcpool-existing"),
            spec=SimpleNamespace(
                version="IPV4",
                visibility="PRIVATE",
                cidrs=["172.16.10.0/24"],
            ),
            status=SimpleNamespace(cidrs=["172.16.10.0/24"]),
        ),
    }

    class _NetworkClient:
        def __init__(self, _sdk):
            pass

        def get(self, request):
            captured["get_network_id"] = request.id
            return _RequestResult(network)

    class _PoolClient:
        def __init__(self, _sdk):
            pass

        def get(self, request):
            captured.setdefault("get_pool_ids", []).append(request.id)
            return _RequestResult(pools[request.id])

        def update(self, _request):
            raise AssertionError("already attached CIDR should not update the parent pool")

    monkeypatch.setattr(cli, "init_nebius_sdk", lambda **_kwargs: _Sdk())
    _patch_vpc_extension_sdk_bindings(
        monkeypatch,
        network_client=_NetworkClient,
        pool_client=_PoolClient,
    )

    result = cli._extend_existing_vpc_parent_private_cidrs(
        project_id="project-1",
        network_id="vpcnetwork-live",
        cidrs=("172.16.10.0/24",),
    )

    assert result == ("10.0.0.0/13", "172.16.10.0/24")
    assert captured["get_network_id"] == "vpcnetwork-live"
    assert captured["get_pool_ids"] == ["vpcpool-default", "vpcpool-existing"]
    assert captured["sdk_closed"] is True


def test_component_field_wizard_can_select_existing_private_pool_for_new_vpc(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    answers = {
        "infra.components[0].inputs.network.name": "workloads-network",
        "infra.components[0].inputs.network.ipv4_private_pool_ids": "1",
        "infra.components[0].inputs.subnets.add": "false",
    }

    class _PoolProviderLookup(ProviderOptionLookup):
        def resolve(self, *, provider, **_kwargs):
            if provider == "project_private_pools":
                return [
                    OptionChoice(
                        value="vpcpool-private",
                        label="vpcpool-private  (default-network-pool) (172.16.0.0/12)",
                    )
                ]
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        for path_label, answer in answers.items():
            if path_label in text:
                return answer
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_PoolProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is True
    assert inputs["network"] == {
        "name": "workloads-network",
        "ipv4_private_pool_ids": ["vpcpool-private"],
    }
    assert "subnets" not in inputs


def test_component_field_wizard_extends_new_vpc_parent_cidrs_for_out_of_pool_subnet(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    captured: list[str] = []
    answers = {
        "infra.components[0].inputs.network.name": "workloads-network",
        "infra.components[0].inputs.network.ipv4_private_cidrs": "1",
        "infra.components[0].inputs.subnets.<new>.name": "workloads",
        "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs": "5",
        "infra.components[0].inputs.subnets.add_another": "false",
    }

    class _EmptyProviderLookup(ProviderOptionLookup):
        def resolve(self, **_kwargs):
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        for path_label, answer in answers.items():
            if path_label in text:
                return answer
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)
    monkeypatch.setattr(
        cli.console, "print", lambda message="", **_kwargs: captured.append(str(message))
    )

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_EmptyProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is True
    assert inputs["network"] == {
        "name": "workloads-network",
        "ipv4_private_cidrs": ["10.8.0.0/13", "172.16.0.0/12"],
    }
    assert inputs["subnets"]["workloads"] == {
        "name": "workloads",
        "use_network_private_pools": False,
        "ipv4_private_cidrs": ["172.16.0.0/12"],
    }
    joined = "\n".join(captured)
    assert "suggested subnet CIDRs" in joined
    assert "192.168.0.0/16  (192.168 parent private block" in joined
    assert "suggested new parent block" in joined
    assert "cxcli adds out-of-parent custom subnet CIDRs to network.ipv4_private_cidrs" in joined
    assert "Extending planned VPC network private CIDRs with 172.16.0.0/12" in joined


def test_component_field_wizard_existing_private_pool_subnet_extends_new_vpc_parent_cidrs(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    answers = {
        "infra.components[0].inputs.network.name": "workloads-network",
        "infra.components[0].inputs.network.ipv4_private_pool_ids": "1",
        "infra.components[0].inputs.subnets.<new>.name": "workloads",
        "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs": "192.168.0.0/16",
        "infra.components[0].inputs.subnets.add_another": "false",
    }

    class _PoolProviderLookup(ProviderOptionLookup):
        def resolve(self, *, provider, **_kwargs):
            if provider == "project_private_pools":
                return [
                    OptionChoice(
                        value="vpcpool-private",
                        label="vpcpool-private  (default-network-pool) (10.0.0.0/13)",
                        metadata={"cidrs": ("10.0.0.0/13",)},
                    )
                ]
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        for path_label, answer in answers.items():
            if path_label in text:
                return answer
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_PoolProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is True
    assert inputs["network"] == {
        "name": "workloads-network",
        "ipv4_private_pool_ids": ["vpcpool-private"],
        "ipv4_private_cidrs": ["192.168.0.0/16"],
    }
    assert inputs["subnets"]["workloads"] == {
        "name": "workloads",
        "use_network_private_pools": False,
        "ipv4_private_cidrs": ["192.168.0.0/16"],
    }


def test_component_field_wizard_existing_vpc_extends_parent_for_out_of_parent_subnet(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    captured: list[str] = []
    extension_calls: list[dict[str, object]] = []

    class _ProviderLookup(ProviderOptionLookup):
        def resolve(self, *, provider, **_kwargs):
            if provider == "project_networks":
                return [
                    OptionChoice(
                        value="vpcnetwork-live",
                        label="vpcnetwork-live  (default-network)",
                        metadata={"private_cidrs": ("10.0.0.0/13",)},
                    )
                ]
            if provider == "project_subnets":
                return [
                    OptionChoice(
                        value="vpcsubnet-live",
                        label="vpcsubnet-live  (existing) (10.0.0.0/16)",
                        metadata={"private_cidrs": ("10.0.0.0/16",)},
                    )
                ]
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        if "infra.components[0].inputs.network.existing_id" in text:
            return "1"
        if "infra.components[0].inputs.subnets.<new>.name" in text:
            return "workloads"
        if "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs" in text:
            return "5"
        if "infra.components[0].inputs.subnets.add_another" in text:
            return "false"
        return "" if default is None else str(default)

    def _extend_parent(**kwargs):
        extension_calls.append(kwargs)
        return ("10.0.0.0/13", "172.16.0.0/12")

    def _phase_decision(prompt_label: str, **_kwargs):
        assert not prompt_label.startswith("Extend the selected live VPC network")
        return cli._WizardPhaseDecision(proceed=True)

    monkeypatch.setattr(cli, "_wizard_continue_phase", _phase_decision)
    monkeypatch.setattr(cli, "_extend_existing_vpc_parent_private_cidrs", _extend_parent)
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)
    monkeypatch.setattr(
        cli.console, "print", lambda message="", **_kwargs: captured.append(str(message))
    )

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_ProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is True
    assert inputs["network"] == {"existing_id": "vpcnetwork-live"}
    assert inputs["subnets"] == {
        "workloads": {
            "name": "workloads",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["172.16.0.0/12"],
        }
    }
    assert extension_calls == [
        {
            "project_id": "project-1",
            "network_id": "vpcnetwork-live",
            "cidrs": ("172.16.0.0/12",),
        }
    ]
    joined = "\n".join(captured)
    assert "attached private pool on the selected live" in joined
    assert "10.1.0.0/16  (subnet child range inside 10.0.0.0/13)" in joined
    assert (
        "172.16.0.0/12  (new parent private block; extends selected live network "
        "attached private pool)"
    ) in joined
    assert (
        "192.168.0.0/16  (192.168 parent private block; extends selected live network "
        "attached private pool)"
    ) in joined
    assert "Live VPC network update required" in joined
    assert "cxcli will add this CIDR to an attached private pool" in joined
    assert "use_network_private_pools=false" in joined
    assert "Extended selected live VPC network attached private pool with 172.16.0.0/12" in joined
    assert "Extend the selected live VPC network private pool now?" not in joined


def test_component_field_wizard_existing_vpc_failed_parent_extension_reprompts_for_subnet_cidr(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    captured: list[str] = []
    cidr_answers = ["192.168.0.0/16", "10.1.0.0/16"]

    class _ProviderLookup(ProviderOptionLookup):
        def resolve(self, *, provider, **_kwargs):
            if provider == "project_networks":
                return [
                    OptionChoice(
                        value="vpcnetwork-live",
                        label="vpcnetwork-live  (default-network)",
                        metadata={"private_cidrs": ("10.0.0.0/13",)},
                    )
                ]
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        if "infra.components[0].inputs.network.existing_id" in text:
            return "1"
        if "infra.components[0].inputs.subnets.<new>.name" in text:
            return "workloads"
        if "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs" in text:
            return cidr_answers.pop(0)
        if "infra.components[0].inputs.subnets.add_another" in text:
            return "false"
        return "" if default is None else str(default)

    def _extend_parent(**_kwargs):
        raise RuntimeError("simulated live extension failure")

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli, "_extend_existing_vpc_parent_private_cidrs", _extend_parent)
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)
    monkeypatch.setattr(
        cli.console, "print", lambda message="", **_kwargs: captured.append(str(message))
    )

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_ProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is True
    assert inputs["network"] == {"existing_id": "vpcnetwork-live"}
    assert inputs["subnets"] == {
        "workloads": {
            "name": "workloads",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["10.1.0.0/16"],
        }
    }
    assert cidr_answers == []
    joined = "\n".join(captured)
    assert "Live VPC network update required" in joined
    assert "simulated live extension failure" in joined


def test_component_field_wizard_existing_vpc_accepts_subnet_cidr_inside_default_parent_pool(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    captured: list[str] = []

    class _ProviderLookup(ProviderOptionLookup):
        def resolve(self, *, provider, **_kwargs):
            if provider == "project_networks":
                return [
                    OptionChoice(
                        value="vpcnetwork-live",
                        label="vpcnetwork-live  (default-network)",
                        metadata={
                            "private_cidrs": (
                                "10.0.0.0/13",
                                "172.16.0.0/12",
                                "192.168.0.0/16",
                            ),
                        },
                    )
                ]
            if provider == "project_subnets":
                return [
                    OptionChoice(
                        value="vpcsubnet-live-1",
                        label="vpcsubnet-live-1  (existing) (10.0.0.0/16)",
                        metadata={"private_cidrs": ("10.0.0.0/16",)},
                    ),
                    OptionChoice(
                        value="vpcsubnet-live-2",
                        label="vpcsubnet-live-2  (existing) (10.2.0.0/16)",
                        metadata={"private_cidrs": ("10.2.0.0/16",)},
                    ),
                ]
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        if "infra.components[0].inputs.network.existing_id" in text:
            return "1"
        if "infra.components[0].inputs.subnets.<new>.name" in text:
            return "workloads"
        if "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs" in text:
            return "1"
        if "infra.components[0].inputs.subnets.add_another" in text:
            return "false"
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)
    monkeypatch.setattr(
        cli.console, "print", lambda message="", **_kwargs: captured.append(str(message))
    )

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_ProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is True
    assert inputs["network"] == {"existing_id": "vpcnetwork-live"}
    assert inputs["subnets"] == {
        "workloads": {
            "name": "workloads",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["10.1.0.0/16"],
        }
    }
    joined = "\n".join(captured)
    assert "10.1.0.0/16  (subnet child range inside 10.0.0.0/13)" in joined
    assert "10.3.0.0/16  (subnet child range inside 10.0.0.0/13)" in joined
    assert "10.4.0.0/16  (subnet child range inside 10.0.0.0/13)" in joined
    assert "10.5.0.0/16  (subnet child range inside 10.0.0.0/13)" in joined
    assert "172.16.0.0/12  (subnet child range inside 172.16.0.0/12)" in joined
    assert "192.168.0.0/16  (subnet child range inside 192.168.0.0/16)" in joined
    assert "172.16.0.0/12  (new parent private block" not in joined
    assert "192.168.0.0/16  (192.168 parent private block" not in joined


def test_component_field_wizard_existing_vpc_avoids_live_private_allocations(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    captured: list[str] = []

    class _ProviderLookup(ProviderOptionLookup):
        def resolve(self, *, provider, **kwargs):
            if provider == "project_networks":
                return [
                    OptionChoice(
                        value="vpcnetwork-live",
                        label="vpcnetwork-live  (default-network)",
                        metadata={
                            "private_cidrs": ("10.0.0.0/13",),
                            "private_pool_ids": ("vpcpool-live",),
                        },
                    )
                ]
            if provider == "project_subnets":
                return [
                    OptionChoice(
                        value="vpcsubnet-inherited",
                        label="vpcsubnet-inherited  (default) (10.0.0.0/13)",
                        metadata={
                            "private_cidrs": (),
                            "use_network_private_pools": True,
                        },
                    )
                ]
            if provider == "project_private_allocations":
                assert kwargs["args"] == {
                    "subnet_ids": ("vpcsubnet-inherited",),
                    "pool_ids": ("vpcpool-live",),
                }
                return [
                    OptionChoice(
                        value="allocation-existing",
                        label="allocation-existing  (10.0.0.42/32)",
                        metadata={
                            "private_cidrs": ("10.0.0.42/32",),
                            "subnet_id": "vpcsubnet-inherited",
                            "pool_id": "vpcpool-live",
                        },
                    )
                ]
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        if "infra.components[0].inputs.network.existing_id" in text:
            return "1"
        if "infra.components[0].inputs.subnets.<new>.name" in text:
            return "workloads"
        if "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs" in text:
            return "1"
        if "infra.components[0].inputs.subnets.add_another" in text:
            return "false"
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)
    monkeypatch.setattr(
        cli.console, "print", lambda message="", **_kwargs: captured.append(str(message))
    )

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_ProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is True
    assert inputs["network"] == {"existing_id": "vpcnetwork-live"}
    assert inputs["subnets"] == {
        "workloads": {
            "name": "workloads",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["10.1.0.0/16"],
        }
    }
    joined = "\n".join(captured)
    assert "10.0.0.0/16  (subnet child range inside 10.0.0.0/13)" not in joined
    assert "10.1.0.0/16  (subnet child range inside 10.0.0.0/13)" in joined
    assert "without live private allocations" in joined


def test_component_field_wizard_existing_vpc_rejects_manual_cidr_over_live_private_allocation(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    captured: list[str] = []
    cidr_answers = ["10.1.0.0/16", "10.2.0.0/16"]

    class _ProviderLookup(ProviderOptionLookup):
        def resolve(self, *, provider, **kwargs):
            if provider == "project_networks":
                return [
                    OptionChoice(
                        value="vpcnetwork-live",
                        label="vpcnetwork-live  (default-network)",
                        metadata={
                            "private_cidrs": ("10.0.0.0/13",),
                            "private_pool_ids": ("vpcpool-live",),
                        },
                    )
                ]
            if provider == "project_subnets":
                return [
                    OptionChoice(
                        value="vpcsubnet-inherited",
                        label="vpcsubnet-inherited  (default) (10.0.0.0/13)",
                        metadata={
                            "private_cidrs": (),
                            "use_network_private_pools": True,
                        },
                    )
                ]
            if provider == "project_private_allocations":
                assert kwargs["args"] == {
                    "subnet_ids": ("vpcsubnet-inherited",),
                    "pool_ids": ("vpcpool-live",),
                }
                return [
                    OptionChoice(
                        value="allocation-existing",
                        label="allocation-existing  (10.1.2.3/32)",
                        metadata={
                            "private_cidrs": ("10.1.2.3/32",),
                            "subnet_id": "vpcsubnet-inherited",
                            "pool_id": "vpcpool-live",
                        },
                    )
                ]
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        if "infra.components[0].inputs.network.existing_id" in text:
            return "1"
        if "infra.components[0].inputs.subnets.<new>.name" in text:
            return "workloads"
        if "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs" in text:
            return cidr_answers.pop(0)
        if "infra.components[0].inputs.subnets.add_another" in text:
            return "false"
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)
    monkeypatch.setattr(
        cli.console, "print", lambda message="", **_kwargs: captured.append(str(message))
    )

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_ProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is True
    assert inputs["network"] == {"existing_id": "vpcnetwork-live"}
    assert inputs["subnets"] == {
        "workloads": {
            "name": "workloads",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["10.2.0.0/16"],
        }
    }
    assert cidr_answers == []
    joined = "\n".join(captured)
    assert "10.1.0.0/16 overlaps 10.1.2.3/32" in joined
    assert "private allocations in the selected VPC network" in joined


def test_component_field_wizard_existing_vpc_rejects_explicit_cidr_when_allocation_lookup_fails(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    captured: list[str] = []
    cidr_answers = ["1", "qq"]

    class _ProviderLookup(ProviderOptionLookup):
        def __init__(self):
            self._last_error = None

        def resolve(self, *, provider, **kwargs):
            self._last_error = None
            if provider == "project_networks":
                return [
                    OptionChoice(
                        value="vpcnetwork-live",
                        label="vpcnetwork-live  (default-network)",
                        metadata={
                            "private_cidrs": ("10.0.0.0/13",),
                            "private_pool_ids": ("vpcpool-live",),
                        },
                    )
                ]
            if provider == "project_subnets":
                return [
                    OptionChoice(
                        value="vpcsubnet-inherited",
                        label="vpcsubnet-inherited  (default) (10.0.0.0/13)",
                        metadata={
                            "private_cidrs": (),
                            "use_network_private_pools": True,
                        },
                    )
                ]
            if provider == "project_private_allocations":
                assert kwargs["args"] == {
                    "subnet_ids": ("vpcsubnet-inherited",),
                    "pool_ids": ("vpcpool-live",),
                }
                self._last_error = "allocation API unavailable"
                return []
            return []

        def last_error(self):
            return self._last_error

    def _answer_prompt(text: str, default=None, **_kwargs):
        if "infra.components[0].inputs.network.existing_id" in text:
            return "1"
        if "infra.components[0].inputs.subnets.<new>.name" in text:
            return "workloads"
        if "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs" in text:
            return cidr_answers.pop(0)
        if "infra.components[0].inputs.subnets.add_another" in text:
            return "false"
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)
    monkeypatch.setattr(
        cli.console, "print", lambda message="", **_kwargs: captured.append(str(message))
    )

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_ProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is False
    assert inputs["network"] == {"existing_id": "vpcnetwork-live"}
    assert inputs["subnets"] == {"workloads": {"name": "workloads"}}
    assert cidr_answers == []
    joined = "\n".join(captured)
    assert "Live private allocation lookup failed" in joined
    assert "could not inspect live private allocations" in joined
    assert "allocation API unavailable" in joined


def test_component_field_wizard_existing_vpc_rejects_explicit_cidr_when_subnet_lookup_fails(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    captured: list[str] = []
    cidr_answers = ["10.0.0.0/16", "qq"]

    class _ProviderLookup(ProviderOptionLookup):
        def __init__(self):
            self._last_error = None

        def resolve(self, *, provider, **_kwargs):
            self._last_error = None
            if provider == "project_networks":
                return [
                    OptionChoice(
                        value="vpcnetwork-live",
                        label="vpcnetwork-live  (default-network)",
                        metadata={
                            "private_cidrs": ("10.0.0.0/13",),
                            "private_pool_ids": ("vpcpool-live",),
                        },
                    )
                ]
            if provider == "project_subnets":
                self._last_error = "subnet API unavailable"
                return []
            if provider == "project_private_allocations":
                return []
            return []

        def last_error(self):
            return self._last_error

    def _answer_prompt(text: str, default=None, **_kwargs):
        if "infra.components[0].inputs.network.existing_id" in text:
            return "1"
        if "infra.components[0].inputs.subnets.<new>.name" in text:
            return "workloads"
        if "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs" in text:
            return cidr_answers.pop(0)
        if "infra.components[0].inputs.subnets.add_another" in text:
            return "false"
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)
    monkeypatch.setattr(
        cli.console, "print", lambda message="", **_kwargs: captured.append(str(message))
    )

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_ProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is False
    assert inputs["network"] == {"existing_id": "vpcnetwork-live"}
    assert inputs["subnets"] == {"workloads": {"name": "workloads"}}
    assert cidr_answers == []
    joined = "\n".join(captured)
    assert "Live subnet lookup failed" in joined
    assert "could not inspect live subnets" in joined
    assert "subnet API unavailable" in joined


def test_component_field_wizard_subnet_custom_cidr_accepts_multiple_ranges(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    captured: list[str] = []
    prompt_texts: list[str] = []
    cidr_answers = ["172.16.30.0/24, 172.16.20.0/24"]

    class _ProviderLookup(ProviderOptionLookup):
        def resolve(self, *, provider, **_kwargs):
            if provider == "project_networks":
                return [
                    OptionChoice(
                        value="vpcnetwork-live",
                        label="vpcnetwork-live  (default-network)",
                        metadata={"private_cidrs": ("172.16.0.0/12",)},
                    )
                ]
            if provider in {"project_subnets", "project_private_allocations"}:
                return []
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        prompt_texts.append(text)
        if "infra.components[0].inputs.network.existing_id" in text:
            return "1"
        if "infra.components[0].inputs.subnets.<new>.name" in text:
            return "workloads"
        if "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs" in text:
            return cidr_answers.pop(0)
        if "infra.components[0].inputs.subnets.add_another" in text:
            return "false"
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)
    monkeypatch.setattr(
        cli.console, "print", lambda message="", **_kwargs: captured.append(str(message))
    )

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_ProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    assert completed is True
    assert inputs["network"] == {"existing_id": "vpcnetwork-live"}
    assert inputs["subnets"] == {
        "workloads": {
            "name": "workloads",
            "use_network_private_pools": False,
            "ipv4_private_cidrs": ["172.16.30.0/24", "172.16.20.0/24"],
        }
    }
    assert cidr_answers == []
    assert any("index or comma-separated CIDRs" in text for text in prompt_texts)
    assert "comma-separated CIDRs are not supported" not in "\n".join(captured)


def test_component_field_wizard_retries_vpc_cidr_overlapping_default_pool(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    prompt_texts: list[str] = []
    cidr_answers = ["10.0.0.0/24", "1"]

    class _EmptyProviderLookup(ProviderOptionLookup):
        def resolve(self, **_kwargs):
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        prompt_texts.append(text)
        if "infra.components[0].inputs.network.name" in text:
            return "workloads-network"
        if "infra.components[0].inputs.network.ipv4_private_cidrs" in text:
            return cidr_answers.pop(0)
        if "infra.components[0].inputs.subnets.<new>.name" in text:
            return "workloads"
        if "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs" in text:
            return "1"
        if "infra.components[0].inputs.subnets.add_another" in text:
            return "false"
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_EmptyProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    network = updated_payload["infra"]["components"][0]["inputs"]["network"]
    subnet = updated_payload["infra"]["components"][0]["inputs"]["subnets"]["workloads"]
    assert completed is True
    assert network["ipv4_private_cidrs"] == ["10.8.0.0/13"]
    assert subnet["ipv4_private_cidrs"] == ["10.8.0.0/16"]
    assert subnet["use_network_private_pools"] is False
    assert cidr_answers == []
    assert sum("inputs.network.ipv4_private_cidrs" in text for text in prompt_texts) == 2


def test_component_field_wizard_retries_overlapping_vpc_cidr_values(
    monkeypatch,
) -> None:
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
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {}},
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )
    prompt_texts: list[str] = []
    cidr_answers = ["172.16.0.0/13,172.16.0.0/12", "2"]

    class _EmptyProviderLookup(ProviderOptionLookup):
        def resolve(self, **_kwargs):
            return []

        def last_error(self):
            return None

    def _answer_prompt(text: str, default=None, **_kwargs):
        prompt_texts.append(text)
        if "infra.components[0].inputs.network.name" in text:
            return "workloads-network"
        if "infra.components[0].inputs.network.ipv4_private_cidrs" in text:
            return cidr_answers.pop(0)
        if "infra.components[0].inputs.subnets.<new>.name" in text:
            return "workloads"
        if "infra.components[0].inputs.subnets.workloads.ipv4_private_cidrs" in text:
            return "1"
        if "infra.components[0].inputs.subnets.add_another" in text:
            return "false"
        return "" if default is None else str(default)

    monkeypatch.setattr(
        cli,
        "_wizard_continue_phase",
        lambda *_args, **_kwargs: cli._WizardPhaseDecision(proceed=True),
    )
    monkeypatch.setattr(cli.typer, "prompt", _answer_prompt)

    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"workloads-vpc"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_EmptyProviderLookup(),
    )

    updated_payload = yaml.safe_load(updated_yaml)
    network = updated_payload["infra"]["components"][0]["inputs"]["network"]
    subnet = updated_payload["infra"]["components"][0]["inputs"]["subnets"]["workloads"]
    assert completed is True
    assert network["ipv4_private_cidrs"] == ["10.16.0.0/13"]
    assert subnet["ipv4_private_cidrs"] == ["10.16.0.0/16"]
    assert subnet["use_network_private_pools"] is False
    assert cidr_answers == []
    assert sum("inputs.network.ipv4_private_cidrs" in text for text in prompt_texts) == 2


def test_vpc_existing_network_choices_do_not_include_planned_vpc_self_reference() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "workloads-vpc",
                    "enabled": True,
                    "inputs": {"network": {"name": "mynetwork"}},
                }
            ]
        }
    }
    entry = ComponentEntry(
        id="vpc",
        scope="infra",
        config_path="infra.components[].inputs",
        description="VPC",
        wizard_fields=BUILTIN_WIZARD_PROFILES["vpc"],
    )

    class _ProviderLookup(ProviderOptionLookup):
        def resolve(self, **kwargs):
            if str(kwargs.get("field_path", "")).endswith(".inputs.network.existing_id"):
                return [OptionChoice(value="vpcnetwork-live", label="default network")]
            return []

    choices = cli._resolve_dynamic_field_choices(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.network.existing_id",
        provider_lookup=_ProviderLookup(),
    )

    assert [choice.value for choice in choices] == ["vpcnetwork-live"]
    assert all("planned:" not in choice.label for choice in choices)


def test_wizard_backtrack_target_skips_current_prompt_left_in_history() -> None:
    first = ("infra", "components", 0, "inputs", "first")
    second = ("infra", "components", 0, "inputs", "second")
    current = ("infra", "components", 0, "inputs", "third")
    prompt_paths = [first, second, current]
    prompt_history = [first, second, current]

    target_index = cli._wizard_backtrack_target_index(
        prompt_paths=prompt_paths,
        prompt_history=prompt_history,
        current_path=current,
    )

    assert target_index == 1
    assert prompt_history == [first]


def test_vm_observability_prompt_guidance_includes_concise_field_comments(
    monkeypatch,
) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        cli.console, "print", lambda message, **_kwargs: captured.append(str(message))
    )
    emitted_guidance: set[str] = set()

    for field_path in (
        "deploy.observability.enabled",
        "deploy.observability.vm.logs.enabled",
    ):
        cli._maybe_print_observability_prompt_guidance(
            full_path_label=field_path,
            emitted_guidance=emitted_guidance,
        )

    joined = "\n".join(captured)
    assert "Compute VMs use the built-in Monitoring agent" in joined
    assert "No VM-side collector package or cxcli-managed service account is installed" in joined
    assert "Collect VM journald logs: answering yes applies" in joined


def test_soperator_rollout_prompt_guidance_includes_concise_field_comments(
    monkeypatch,
) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        cli.console, "print", lambda message, **_kwargs: captured.append(str(message))
    )
    answers = {
        "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout.strategy": (
            "safe-surge"
        ),
        "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout.wave_budget": (
            "groups"
        ),
        "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout.worker_wave_groups": (
            "2"
        ),
        "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout.max_parallel_worker_groups": (
            "2"
        ),
        "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout.worker_group_strategy.max_surge_count": (
            "1"
        ),
        "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout.worker_group_strategy.max_unavailable_count": (
            "0"
        ),
        "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout.worker_group_strategy.drain_timeout": (
            "30m"
        ),
    }

    def _prompt_scalar(field_label: str, default: object, **_kwargs: object) -> tuple[object, bool]:
        return answers.get(field_label, default), False

    monkeypatch.setattr(cli, "_prompt_scalar_override", _prompt_scalar)

    manifest = cli._prompt_soperator_onboarding_rollout_manifest(
        {
            "soperator_onboarding": {
                "node_template_upgrade": {
                    "rollout": {
                        "strategy": "safe-surge",
                        "worker_wave_groups": 1,
                        "worker_group_strategy": {
                            "max_surge_count": 1,
                            "max_unavailable_count": 0,
                            "drain_timeout": "30m",
                        },
                    }
                }
            }
        }
    )

    assert manifest == {
        "strategy": "safe-surge",
        "worker_wave_groups": 2,
        "max_parallel_worker_groups": 2,
        "worker_group_strategy": {
            "max_surge_count": 1,
            "max_unavailable_count": 0,
            "drain_timeout": "30m",
        },
    }
    joined = "\n".join(captured)
    assert "Strategy: zero-surge is the default" in joined
    assert "safe-surge preserves capacity with temporary surge nodes" in joined
    assert "Safe-surge wave budget: choose groups for a fixed batch size" in joined
    assert "Safe-surge worker wave groups: number of worker groups updated per wave" in joined
    assert "Max parallel worker groups: optional hard cap" in joined
    assert "Max surge count: temporary extra nodes per worker group" in joined
    assert "Max unavailable count: nodes per worker group allowed down" in joined
    assert "Drain timeout: time to wait for pod eviction" in joined


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
        path_label="infra.components[0].inputs.cluster.k8s_version",
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
    captured: dict[str, Any] = {}

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


def test_prompt_choice_override_non_tty_can_leave_auto_choice_unset(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    captured: dict[str, Any] = {}

    def _fake_prompt(text: str, default=None):
        captured["text"] = text
        captured["default"] = default
        return ""

    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.node_groups.system.boot_disk.type",
        current="NETWORK_SSD",
        choices=[
            OptionChoice(value="NETWORK_SSD", label="NETWORK_SSD"),
            OptionChoice(value="NETWORK_SSD_NON_REPLICATED", label="NETWORK_SSD_NON_REPLICATED"),
        ],
        type_hint="string",
        required=False,
        unset_on_skip=True,
    )

    assert should_stop is False
    assert value is None
    assert captured["default"] == ""
    assert "blank keeps unset" in str(captured["text"])


@pytest.mark.parametrize(
    ("current", "type_hint"),
    [
        ("auto-filled", "string"),
        (True, "bool"),
        (3, "number"),
        (1.5, "number"),
        ({"key": "value"}, "object"),
    ],
)
def test_prompt_scalar_override_unset_on_skip_leaves_non_choice_scalars_unset(
    monkeypatch,
    current,
    type_hint,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    captured: dict[str, Any] = {}

    def _fake_prompt(text: str, default=None):
        captured["text"] = text
        captured["default"] = default
        return ""

    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.optional_field",
        current,
        type_hint=type_hint,
        required=False,
        unset_on_skip=True,
    )

    assert should_stop is False
    assert value is None
    assert captured["default"] == ""
    assert "blank keeps unset" in str(captured["text"])


def test_soperator_bulk_apply_prompt_uses_short_label_and_true_default(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    captured: dict[str, Any] = {}

    def _fake_prompt(text: str, default=None):
        captured["text"] = text
        captured["default"] = default
        return default

    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)

    value, should_stop = cli._prompt_scalar_override(
        "infra.components[0].inputs.soperator.worker_node_groups.all_worker_shards_apply_to_all",
        True,
        type_hint="bool",
        required=False,
        unset_on_skip=False,
    )

    assert should_stop is False
    assert value is True
    assert "all_worker_shards_apply_to_all" in str(captured["text"])
    assert "all_worker_shards.apply_to_all" not in str(captured["text"])
    assert captured["default"] == "true"


def test_soperator_bulk_prompt_comment_explains_apply_to_all(monkeypatch) -> None:
    messages: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda message: messages.append(str(message)))

    cli._maybe_print_soperator_worker_bulk_prompt_comment(
        "infra.components[0].inputs.soperator.worker_node_groups.all_worker_shards_apply_to_all"
    )

    assert messages == [
        "[dim]Bulk worker shard choice: true applies one autoscaling/ephemeral "
        "choice to all worker shards; false asks each shard separately.[/dim]"
    ]


def test_upgrade_node_template_node_group_prompt_mentions_blank_selects_all(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_upgrade_interactive_prompts_enabled", lambda: True)
    captured: dict[str, Any] = {}

    def _fake_prompt(text: str, default=None):
        captured["text"] = text
        captured["default"] = default
        return ""

    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)

    value = cli._prompt_upgrade_node_group_if_guided(
        path_prefix="upgrade.node_template",
        guided=True,
        node_group="",
    )

    assert value == ""
    assert captured["default"] == ""
    assert "blank = all managed node groups" in str(captured["text"])
    assert "blank keeps unset" not in str(captured["text"])


def test_upgrade_strategy_choice_explains_safe_surge_default_spare_node(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_upgrade_interactive_prompts_enabled", lambda: True)
    captured: dict[str, Any] = {}

    def _fake_prompt(path_label: str, current: object, **kwargs: object):
        del current
        if path_label == "upgrade.node_template.strategy":
            captured["choices"] = kwargs["choices"]
            return cli.DISRUPTION_POLICY_SAFE, False
        if path_label == "upgrade.node_template.strategy_max_surge_count":
            return 1, False
        if path_label == "upgrade.node_template.drain_timeout":
            return "auto", False
        raise AssertionError(f"unexpected prompt: {path_label}")

    monkeypatch.setattr(cli, "_prompt_scalar_override", _fake_prompt)

    cli._prompt_upgrade_disruption_options_if_guided(
        path_prefix="upgrade.node_template",
        guided=True,
        dry_run=True,
        disruption_policy=cli.DISRUPTION_POLICY_ALLOW_UNAVAILABLE,
        drain_timeout="auto",
        strategy_max_surge_count=None,
        skip_validations=True,
    )

    labels = [choice.label for choice in captured["choices"]]
    assert (
        "safe-surge  (default 1 spare node per active node group; preserves active capacity)"
        in labels
    )
    assert "force-delete  (last resort; auto=10m, then deletes remaining Pods and node)" in labels


def test_upgrade_drain_timeout_prompt_mentions_auto_strategy_default(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_upgrade_interactive_prompts_enabled", lambda: True)
    captured: dict[str, tuple[str, object]] = {}

    def _fake_prompt(text: str, default=None):
        label = str(text)
        if "strategy_max_surge_count" in label:
            captured["strategy_max_surge_count"] = (label, default)
            return "1"
        captured["drain_timeout"] = (label, default)
        return "auto"

    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)

    dry_run, policy, drain_timeout, strategy_max_surge_count, skip_validations = (
        cli._prompt_upgrade_disruption_options_if_guided(
            path_prefix="upgrade.node_template",
            guided=True,
            dry_run=True,
            disruption_policy=cli.DISRUPTION_POLICY_SAFE,
            drain_timeout="auto",
            strategy_max_surge_count=None,
            skip_validations=True,
        )
    )

    assert (dry_run, policy, drain_timeout, strategy_max_surge_count, skip_validations) == (
        True,
        cli.DISRUPTION_POLICY_SAFE,
        "auto",
        1,
        True,
    )
    surge_prompt, surge_default = captured["strategy_max_surge_count"]
    assert surge_default == "1"
    assert "temporary extra nodes per active node group" in surge_prompt
    drain_prompt, drain_default = captured["drain_timeout"]
    assert drain_default == "auto"
    assert "auto = 30m for zero-surge/safe-surge; 10m for force-delete" in drain_prompt


def test_upgrade_drain_timeout_backtracks_to_strategy_prompt(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_upgrade_interactive_prompts_enabled", lambda: True)
    responses: dict[str, list[object]] = {
        "upgrade.node_template.strategy": [
            cli.DISRUPTION_POLICY_ALLOW_UNAVAILABLE,
            cli.DISRUPTION_POLICY_SAFE,
        ],
        "upgrade.node_template.strategy_max_surge_count": [1],
        "upgrade.node_template.drain_timeout": [cli._WIZARD_BACKTRACK, "auto"],
    }
    prompted_paths: list[str] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint: str | None = None,
        required: bool = False,
        unset_on_skip: bool = False,
        **_kwargs: object,
    ) -> tuple[object, bool]:
        del current, choices, type_hint, required, unset_on_skip
        prompted_paths.append(path_label)
        return responses[path_label].pop(0), False

    monkeypatch.setattr(cli, "_prompt_scalar_override", _fake_prompt)

    dry_run, policy, drain_timeout, strategy_max_surge_count, skip_validations = (
        cli._prompt_upgrade_disruption_options_if_guided(
            path_prefix="upgrade.node_template",
            guided=True,
            dry_run=True,
            disruption_policy=cli.DISRUPTION_POLICY_ALLOW_UNAVAILABLE,
            drain_timeout="auto",
            strategy_max_surge_count=None,
            skip_validations=True,
        )
    )

    assert (dry_run, policy, drain_timeout, strategy_max_surge_count, skip_validations) == (
        True,
        cli.DISRUPTION_POLICY_SAFE,
        "auto",
        1,
        True,
    )
    assert prompted_paths == [
        "upgrade.node_template.strategy",
        "upgrade.node_template.drain_timeout",
        "upgrade.node_template.strategy",
        "upgrade.node_template.strategy_max_surge_count",
        "upgrade.node_template.drain_timeout",
    ]


def test_upgrade_drain_timeout_backtracks_to_safe_surge_count_prompt(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_upgrade_interactive_prompts_enabled", lambda: True)
    responses: dict[str, list[object]] = {
        "upgrade.node_template.strategy": [cli.DISRUPTION_POLICY_SAFE],
        "upgrade.node_template.strategy_max_surge_count": [2, 3],
        "upgrade.node_template.drain_timeout": [cli._WIZARD_BACKTRACK, "auto"],
    }
    prompted_paths: list[str] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint: str | None = None,
        required: bool = False,
        unset_on_skip: bool = False,
        **_kwargs: object,
    ) -> tuple[object, bool]:
        del current, choices, type_hint, required, unset_on_skip
        prompted_paths.append(path_label)
        return responses[path_label].pop(0), False

    monkeypatch.setattr(cli, "_prompt_scalar_override", _fake_prompt)

    dry_run, policy, drain_timeout, strategy_max_surge_count, skip_validations = (
        cli._prompt_upgrade_disruption_options_if_guided(
            path_prefix="upgrade.node_template",
            guided=True,
            dry_run=True,
            disruption_policy=cli.DISRUPTION_POLICY_ALLOW_UNAVAILABLE,
            drain_timeout="auto",
            strategy_max_surge_count=None,
            skip_validations=True,
        )
    )

    assert (dry_run, policy, drain_timeout, strategy_max_surge_count, skip_validations) == (
        True,
        cli.DISRUPTION_POLICY_SAFE,
        "auto",
        3,
        True,
    )
    assert prompted_paths == [
        "upgrade.node_template.strategy",
        "upgrade.node_template.strategy_max_surge_count",
        "upgrade.node_template.drain_timeout",
        "upgrade.node_template.strategy_max_surge_count",
        "upgrade.node_template.drain_timeout",
    ]


def test_prompt_choice_override_tty_keeps_skip_for_optional_recommended_default(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, Any] = {}

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


def test_prompt_choice_override_tty_keeps_current_for_optional_existing_value(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, Any] = {}

    class _FakePrompt:
        def ask(self):
            return "__skip__"

    def _fake_select(*args, **kwargs):
        captured["choices"] = kwargs.get("choices")
        return _FakePrompt()

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        select=_fake_select,
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.node_groups.system.boot_disk.type",
        current="NETWORK_SSD",
        choices=[
            OptionChoice(value="NETWORK_SSD", label="NETWORK_SSD"),
            OptionChoice(value="NETWORK_SSD_NON_REPLICATED", label="NETWORK_SSD_NON_REPLICATED"),
        ],
        type_hint="string",
        required=False,
    )

    assert should_stop is False
    assert value == "NETWORK_SSD"
    titles = [choice["title"] for choice in captured["choices"]]
    assert titles[0] == "<keep current / skip>"


def test_prompt_choice_override_tty_can_hide_skip_choice_for_semantic_none(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, Any] = {}

    class _FakePrompt:
        def ask(self):
            return "none"

    def _fake_select(*args, **kwargs):
        captured["choices"] = kwargs.get("choices")
        return _FakePrompt()

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        select=_fake_select,
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.node_groups.system.service_account.mode",
        current="none",
        choices=[
            OptionChoice(value="none", label="none  (do not assign a service account)"),
            OptionChoice(
                value="existing_id", label="existing_id  (use existing service account ID)"
            ),
            OptionChoice(
                value="create_name", label="create_name  (create service account by name)"
            ),
        ],
        type_hint="string",
        required=False,
    )

    assert should_stop is False
    assert value == "none"
    titles = [choice["title"] for choice in captured["choices"]]
    assert titles == [
        "none  (do not assign a service account)",
        "existing_id  (use existing service account ID)",
        "create_name  (create service account by name)",
    ]


def test_prompt_choice_override_tty_can_leave_optional_auto_choice_unset(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    captured: dict[str, Any] = {}

    class _FakePrompt:
        def ask(self):
            return "__skip__"

    def _fake_select(*args, **kwargs):
        captured["choices"] = kwargs.get("choices")
        return _FakePrompt()

    fake_questionary = SimpleNamespace(
        Choice=lambda **kwargs: kwargs,
        select=_fake_select,
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)

    value, should_stop = cli._prompt_choice_override(
        path_label="infra.components[0].inputs.node_groups.system.boot_disk.type",
        current="NETWORK_SSD",
        choices=[
            OptionChoice(value="NETWORK_SSD", label="NETWORK_SSD"),
            OptionChoice(value="NETWORK_SSD_NON_REPLICATED", label="NETWORK_SSD_NON_REPLICATED"),
        ],
        type_hint="string",
        required=False,
        unset_on_skip=True,
    )

    assert should_stop is False
    assert value is None
    titles = [choice["title"] for choice in captured["choices"]]
    assert titles[0] == "<skip / keep unset>"


def test_maybe_print_gpu_preset_prompt_guidance_for_gpu_shape(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda message: captured.append(str(message)))

    cli._maybe_print_gpu_preset_prompt_guidance(
        payload={
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "node_group_defaults": {
                                "gpu": {
                                    "platform": "gpu-h100-sxm",
                                }
                            },
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
        full_path_label="infra.components[0].inputs.node_group_defaults.gpu.preset",
        emitted_guidance=set(),
    )

    assert any("Ethernet-only" in message for message in captured)


def test_maybe_print_gpu_preset_prompt_guidance_lists_live_capacity_rows(
    monkeypatch,
) -> None:
    captured: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda message: captured.append(str(message)))

    def _availability(available: int) -> CapacityAdviceAvailability:
        return CapacityAdviceAvailability(
            available=available,
            limit=available,
            availability_level="AVAILABILITY_LEVEL_HIGH",
            data_state="DATA_STATE_FRESH",
        )

    provider_lookup: Any = SimpleNamespace(
        compute_platform_capacity_advice=lambda **_kwargs: (
            CapacityResourceAdvice(
                region="us-central1",
                platform="gpu-h100-sxm",
                preset="8gpu-128vcpu-1600gb",
                fabric="fabric-a",
                on_demand=_availability(4),
                reserved=_availability(1),
                preemptible=_availability(0),
                gpu_count=8,
            ),
            CapacityResourceAdvice(
                region="us-central1",
                platform="gpu-h100-sxm",
                preset="8gpu-128vcpu-1600gb",
                fabric="fabric-b",
                on_demand=_availability(2),
                reserved=_availability(0),
                preemptible=_availability(0),
                gpu_count=8,
            ),
            CapacityResourceAdvice(
                region="us-central1",
                platform="gpu-h100-sxm",
                preset="1gpu-16vcpu-200gb",
                fabric="",
                on_demand=_availability(0),
                reserved=_availability(0),
                preemptible=_availability(0),
                gpu_count=1,
            ),
        )
    )
    emitted_guidance: set[str] = set()

    cli._maybe_print_gpu_preset_prompt_guidance(
        payload={
            "client_info": {
                "nebius": {
                    "tenant_id": "tenant-123",
                    "region_id": "us-central1",
                }
            },
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "node_group_defaults": {
                                "gpu": {
                                    "platform": "gpu-h100-sxm",
                                }
                            },
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
        full_path_label="infra.components[0].inputs.node_group_defaults.gpu.preset",
        provider_lookup=provider_lookup,
        emitted_guidance=emitted_guidance,
    )

    output = "\n".join(captured)
    assert "Live GPU capacity for gpu-h100-sxm in us-central1" in output
    assert "fabric-a" in output
    assert "fabric-b" in output
    assert "regular-vm" in output
    assert "4 VMs (4 x 8-GPU = 32 GPUs)" in output
    assert "1 VM (1 x 8-GPU = 8 GPUs)" in output
    assert "1gpu-16vcpu-200gb" not in output

    captured.clear()
    cli._maybe_print_gpu_preset_prompt_guidance(
        payload={
            "client_info": {
                "nebius": {
                    "tenant_id": "tenant-123",
                    "region_id": "us-central1",
                }
            },
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "node_group_defaults": {
                                "gpu": {
                                    "platform": "gpu-h100-sxm",
                                    "reservation": {"policy": "FORBID"},
                                }
                            },
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
        full_path_label="infra.components[0].inputs.node_group_defaults.gpu.preset",
        provider_lookup=provider_lookup,
        emitted_guidance=emitted_guidance,
    )

    forbid_output = "\n".join(captured)
    assert "fabric-a" in forbid_output
    assert "fabric-b" in forbid_output
    assert "regular-vm" in forbid_output
    assert "reserved" not in forbid_output
    assert "1gpu-16vcpu-200gb" not in forbid_output


def test_maybe_print_selected_gpu_preset_guidance_for_single_gpu_shape(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda message: captured.append(str(message)))

    provider_lookup: Any = SimpleNamespace(
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
                            "node_group_defaults": {
                                "gpu": {
                                    "platform": "gpu-h100-sxm",
                                    "preset": "1gpu-16vcpu-200gb",
                                }
                            },
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
        full_path_label="infra.components[0].inputs.node_group_defaults.gpu.preset",
        provider_lookup=provider_lookup,
        emitted_guidance=set(),
    )

    assert any("not production distributed training" in message for message in captured)


def test_maybe_print_selected_gpu_preset_guidance_for_vm_single_gpu_shape(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(cli.console, "print", lambda message: captured.append(str(message)))

    provider_lookup: Any = SimpleNamespace(
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

    provider_lookup: Any = SimpleNamespace(last_error=lambda: None)
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
    captured: dict[str, Any] = {}

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
    captured: dict[str, Any] = {}

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
