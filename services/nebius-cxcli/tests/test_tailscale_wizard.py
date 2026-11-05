"""Exercise Tailscale guidance and the real shared text/menu prompt paths."""

import copy
import json

import pytest

from nebius_cxcli import cli, tailscale_config, tailscale_wizard
from test_tailscale import row


def answers(monkeypatch, values=None):
    iterator = iter(values) if values is not None else None
    prompts = []
    messages = []
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(
        cli.console, "print", lambda *a, **k: messages.append(" ".join(map(str, a)))
    )

    def prompt(label, default=None, **kwargs):
        prompts.append(str(label))
        answer = next(iterator) if iterator is not None else ""
        return answer or default or ""

    monkeypatch.setattr(cli.typer, "prompt", prompt)
    return prompts, messages


def test_new_vpn_rule_retries_arrow_input_and_skips_protocol_ports(monkeypatch):
    prompts, messages = answers(monkeypatch, ["", "\x1b[B", "group:developers", "", "", ""])
    value, stop = tailscale_wizard.prompt_rules([], row(), "tailscale.policy.rules")
    assert not stop
    assert value == [
        {
            "sources": ["group:developers"],
            "destinations": ["10.20.0.0/24"],
            "protocol": "all",
            "ports": [],
        }
    ]
    assert not any(".protocol" in p or ".ports" in p for p in prompts)
    assert sum(".sources" in p for p in prompts) == 2
    assert any("all protocols and ports" in m and "Requested access:" in m for m in messages)
    assert any("Control characters" in m for m in messages)
    assert not any("\x1b" in m for m in messages)


def test_saved_mixed_rules_keep_pairings_when_routes_expand(monkeypatch):
    answers(monkeypatch)
    gateway = row()
    saved = gateway["tailscale"]["policy"]["rules"]
    saved[0]["destinations"] = ["10.20.0.5/32"]
    saved.append(
        {"sources": ["group:ops"], "destinations": ["10.20.0.0/25"], "protocol": "all", "ports": []}
    )
    before = copy.deepcopy(saved)
    gateway["tailscale"]["routes"].append("10.21.0.0/24")
    value, stop = tailscale_wizard.prompt_rules(saved, gateway, "tailscale.policy.rules")
    assert not stop
    assert value == saved == before


def test_tty_destinations_retry_cleared_selection_without_restoring_scope(monkeypatch):
    from types import SimpleNamespace

    import questionary

    _, messages = answers(monkeypatch)
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    monkeypatch.setattr(
        questionary, "checkbox", lambda *a, **kw: SimpleNamespace(choices=kw["choices"])
    )
    responses = iter([[], ["10.20.0.5/32"]])
    checked = []

    def select(question):
        checked.append([c.value for c in question.choices if c.checked])
        return next(responses)

    monkeypatch.setattr(cli, "_ask_questionary_with_wizard_navigation", select)
    current = ["10.20.0.5/32"]
    value, stop = tailscale_wizard._prompt_destinations(
        current, ["10.20.0.0/24", "10.21.0.0/24"], "tailscale.policy.rules[0].destinations"
    )
    assert not stop and value == current == ["10.20.0.5/32"]
    assert checked == [current, current]
    assert any("Select at least one destination" in message for message in messages)


@pytest.mark.parametrize("token", [cli._WIZARD_BACK_CHOICE, cli._WIZARD_QUIT_CHOICE])
def test_tty_destination_navigation_preserves_saved_scope(monkeypatch, token):
    import questionary

    answers(monkeypatch)
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    monkeypatch.setattr(questionary, "checkbox", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "_ask_questionary_with_wizard_navigation", lambda _: token)
    current = ["10.20.0.5/32"]
    value, stop = tailscale_wizard._prompt_destinations(
        current, ["10.20.0.0/24"], "tailscale.policy.rules[0].destinations"
    )
    assert stop if token == cli._WIZARD_QUIT_CHOICE else cli._wizard_backtrack_requested(value)
    assert current == ["10.20.0.5/32"]


@pytest.mark.parametrize("protocol,ports", [("tcp", ["443"]), ("udp", ["53"]), ("icmp", [])])
def test_detailed_rules_use_real_string_list_parser(monkeypatch, protocol, ports):
    responses = ["services", "group:users", "", protocol]
    responses += ports + ["", ""]
    prompts, _ = answers(monkeypatch, responses)
    value, stop = tailscale_wizard.prompt_rules([], row(), "tailscale.policy.rules")
    assert not stop
    assert value[0]["protocol"] == protocol
    assert value[0]["ports"] == ports
    assert any(".ports" in p for p in prompts) == bool(ports)


def test_manual_narrow_destination_and_rejected_summary_retain_draft(monkeypatch):
    answers(
        monkeypatch,
        ["", "group:ops", "__manual__", "10.20.0.5/32", "false", "", "", "", "true", "false"],
    )
    value, stop = tailscale_wizard.prompt_rules([], row(), "tailscale.policy.rules")
    assert not stop
    assert value[0]["destinations"] == ["10.20.0.5/32"]
    assert value[0]["sources"] == ["group:ops"]


@pytest.mark.parametrize("token", ["q", "qq"])
@pytest.mark.parametrize("position", [0, 1, 2, 3, 4])
def test_rule_navigation_never_mutates_saved_intent(monkeypatch, token, position):
    gateway = row()
    saved = [
        {"sources": ["group:ops"], "destinations": ["10.20.0.0/25"], "protocol": "all", "ports": []}
    ]
    before = copy.deepcopy(saved)
    answers(monkeypatch, [""] * position + [token])
    value, stop = tailscale_wizard.prompt_rules(saved, gateway, "tailscale.policy.rules")
    assert stop if token == "qq" else cli._wizard_backtrack_requested(value)
    assert saved == before


@pytest.mark.parametrize("storage", ["new", "existing", "reference"])
def test_auth_choices_describe_and_store_references_only(monkeypatch, storage):
    _, messages = answers(monkeypatch, [storage] + (["secret-id"] if storage == "existing" else []))
    gateway = row()
    auth, stop = tailscale_wizard.prompt_auth({}, gateway, {}, "tailscale.auth")
    assert not stop
    assert auth["mode"] == "oauth"
    if storage == "reference":
        assert auth == {"mode": "oauth"}
    elif storage == "new":
        assert auth["secretstash"] == {
            "project_id": "project-test",
            "component_instance": "ts-oauth",
            "secret_name": "ts-oauth",
        }
    else:
        assert auth["secretstash"] == {"project_id": "project-test", "secret_id": "secret-id"}
    assert "client_secret" not in auth
    assert any("empty Nebius secret container" in m and "owner-only" in m for m in messages)
    gateway["tailscale"]["auth"] = auth
    tailscale_config.validate(gateway)


def test_existing_auth_references_survive_accepting_defaults(monkeypatch):
    answers(monkeypatch)
    gateway = row()
    original = gateway["tailscale"]["auth"]
    result, stop = tailscale_wizard.prompt_auth(original, gateway, {}, "tailscale.auth")
    assert not stop and result == original


def test_unconfigured_oauth_defaults_to_new_container(monkeypatch):
    answers(monkeypatch)
    result, stop = tailscale_wizard.prompt_auth({"mode": "oauth"}, row(), {}, "tailscale.auth")
    assert not stop and result["secretstash"]["component_instance"] == "ts-oauth"


@pytest.mark.parametrize("linked", [True, False])
def test_saved_secretstash_identity_and_version_survive_defaults(monkeypatch, linked):
    answers(monkeypatch)
    auth = {
        "mode": "oauth",
        "secretstash": {
            "project_id": "project-test",
            "secret_id": "existing-secret",
            "version_id": "saved-version",
            "nebius_auth": {"profile": "operator"},
        },
    }
    if linked:
        auth["secretstash"].update(
            component_instance="shared-oauth", secret_name="router-credential"
        )
    original = copy.deepcopy(auth)
    result, stop = tailscale_wizard.prompt_auth(auth, row(), {}, "tailscale.auth")
    assert not stop and result == auth == original


def test_continuation_uses_guidance_and_real_rule_editor(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from nebius_cxcli import tailscale_onboarding, tailscale_onboarding_cli

    _, messages = answers(monkeypatch)
    text_prompt = cli.typer.prompt
    defaults = {}
    tag_answers = iter(["router", "tag:router, tag:router", "tag:router"])

    def prompt(label, default=None, **kwargs):
        defaults[str(label).split(" [", 1)[0]] = default
        if str(label).startswith("tailscale.tags ["):
            return next(tag_answers)
        return text_prompt(label, default=default, **kwargs)

    monkeypatch.setattr(cli.typer, "prompt", prompt)
    gateway = row()
    gateway["tailscale"]["policy"]["rules"][0].update(protocol="all", ports=[])
    payload = {"infra": {"components": [gateway]}}
    config_path = tmp_path / "config.yaml"
    config_path.write_text("fixture")
    paths = SimpleNamespace(config_path=config_path, project_dir=tmp_path)
    writes = []
    monkeypatch.setattr(tailscale_onboarding_cli, "load", lambda *a: (cli, {}, paths, gateway))
    monkeypatch.setattr(cli, "_load_config_payload", lambda p: copy.deepcopy(payload))
    monkeypatch.setattr(cli, "_write_runtime_payload_config", lambda p, v, **k: writes.append(v))
    monkeypatch.setattr(cli.typer, "confirm", lambda *a, **k: True)
    tailscale_onboarding_cli.onboarding_configure.__wrapped__(
        config_path,
        interactive=True,
        approval_reference="ticket",
    )
    saved = writes[0]["infra"]["components"][0]
    assert saved["tailscale"]["tags"] == ["tag:router"]
    assert any("must use tag:<name>" in m for m in messages)
    assert any("duplicate entries" in m for m in messages)
    assert saved["tailscale"]["policy"]["rules"] == gateway["tailscale"]["policy"]["rules"]
    tailscale_onboarding.assert_accepted(saved)
    assert any("Owner email:" in m for m in messages)
    assert any("Purpose:" in m for m in messages)
    assert any("Credential storage:" in m for m in messages)
    assert any("Requested access:" in m for m in messages)
    assert defaults["tailscale.policy.tag_owners"] == "[]"
    assert saved["tailscale"]["policy"]["tag_owners"] == []


def test_guidance_covers_management_and_handoff_fields(monkeypatch):
    _, messages = answers(monkeypatch)
    for suffix in tailscale_wizard.GUIDANCE:
        tailscale_wizard.print_guidance("infra.components[7]." + suffix)
    assert len(messages) == len(tailscale_wizard.GUIDANCE)
    assert "TCP port 22" in messages[0] and "source IPv4" in messages[0]
    assert any("grants no permissions" in m for m in messages)


@pytest.mark.parametrize("terminal", [True, False])
def test_guidance_is_dim_unhighlighted_and_describes_proposed_tags_and_email(monkeypatch, terminal):
    import io
    import re

    from rich.console import Console

    output = io.StringIO()
    monkeypatch.setattr(
        cli,
        "console",
        Console(
            file=output,
            force_terminal=terminal,
            width=200,
            color_system="standard" if terminal else None,
        ),
    )
    tailscale_wizard.print_guidance("tailscale.tags")
    tailscale_wizard.print_guidance("tailscale.onboarding.owner")
    tailscale_wizard.print_guidance("inputs.management_cidrs")
    tailscale_wizard.print_auth_guidance()
    text = output.getvalue()
    codes = [part for code in re.findall(r"\x1b\[([0-9;]*)m", text) for part in code.split(";")]
    assert ("2" in codes) == terminal
    assert "1" not in codes
    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    assert "approval before activation" in plain
    assert "shared" in plain and "not checked" in plain
    assert "Owner email:" in plain and "owner@example.com" in plain
    assert "monitored team" in plain and "grants no permissions" in plain


def test_optional_empty_default_is_rendered_in_prompt_brackets():
    import typer
    from typer.testing import CliRunner

    app = typer.Typer()

    @app.command()
    def prompt():
        value, stop = cli._prompt_scalar_override(
            "tailscale.policy.tag_owners", [], type_hint="list(string)", show_empty_default=True
        )
        assert value == [] and not stop

    result = CliRunner().invoke(app, [], input="\n")
    assert result.exit_code == 0
    assert "[[]]:" in result.output


@pytest.mark.parametrize(
    "saved_name,saved_owners", [(None, None), ("custom-router", ["group:ops"]), ("router", [])]
)
def test_initial_setup_orders_routes_before_real_policy_editor(
    monkeypatch, saved_name, saved_owners
):
    import yaml

    from nebius_cxcli import wireguard_subnets
    from nebius_cxcli.components import component_entries
    from nebius_cxcli.provider_options import OptionChoice, ProviderOptionLookup
    from test_render import _starter_payload

    payload = _starter_payload(selected_infra={"tailscale-gw"}, selected_apps=set())
    gateway = row()
    gateway.pop("tailscale")
    gateway["inputs"].pop("name")
    if saved_name is not None:
        gateway["inputs"]["name"] = saved_name
    if saved_owners is not None:
        gateway["tailscale"] = {"policy": {"tag_owners": saved_owners}}
    payload["infra"]["components"] = [gateway]
    entry = next(e for e in component_entries("infra") if e.id == "tailscale-gw")
    events = []
    defaults = {}
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: events.append(" ".join(map(str, a))))
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(
        cli, "_wizard_continue_phase", lambda *a, **k: cli._WizardPhaseDecision(proceed=True)
    )
    monkeypatch.setattr(wireguard_subnets, "live_subnet_choices", lambda *a: [])
    monkeypatch.setattr(
        cli,
        "_prompt_ssh_public_key_override",
        lambda *a, **k: (gateway["inputs"]["ssh_public_key"], False),
    )

    def choices(**kwargs):
        key = kwargs["full_path_label"].rsplit(".", 1)[-1]
        value = gateway["inputs"].get(key)
        return (
            [OptionChoice(value, value)]
            if key in {"network_id", "subnet_id", "platform", "preset"}
            else []
        )

    monkeypatch.setattr(cli, "_resolve_dynamic_field_choices", choices)
    tag_answers = iter(["router", "tag:router, tag:router", "tag:router"])

    def prompt(label, default=None, **kwargs):
        events.append(str(label))
        defaults[str(label).split(" [", 1)[0]] = default
        if ".tailscale.tags [" in str(label):
            return next(tag_answers)
        for field, answer in {
            ".tailscale.tailnet": "example.ts.net",
            ".tailscale.tags": "tag:router",
            ".tailscale.routes": "10.20.0.0/24",
            ".onboarding.owner": "owner@example.com",
            ".onboarding.purpose": "Private cluster access",
            ".sources": "group:users",
        }.items():
            if field in label:
                return answer
        return default or ""

    monkeypatch.setattr(cli.typer, "prompt", prompt)
    text, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload),
        selected_infra={"ts"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=ProviderOptionLookup(),
    )
    assert completed
    saved = yaml.safe_load(text)["infra"]["components"][0]
    tailscale_config.validate(saved)
    assert saved["tailscale"]["tags"] == ["tag:router"]
    assert any("must use tag:<name>" in m for m in events)
    assert any("duplicate entries" in m for m in events)
    assert saved["inputs"]["name"] == (saved_name or "tailscale-gw")
    assert saved["tailscale"]["policy"]["tag_owners"] == (saved_owners or [])
    name_default = next(value for key, value in defaults.items() if key.endswith(".inputs.name"))
    owners_default = next(
        value for key, value in defaults.items() if key.endswith(".policy.tag_owners")
    )
    assert name_default == (saved_name or "tailscale-gw")
    assert owners_default == json.dumps(saved_owners or [])
    assert saved["tailscale"]["policy"]["rules"][0] == {
        "sources": ["group:users"],
        "destinations": ["10.20.0.0/24"],
        "protocol": "all",
        "ports": [],
    }
    route_index = next(i for i, message in enumerate(events) if ".tailscale.routes [" in message)
    rule_index = next(i for i, message in enumerate(events) if ".access_level" in message)
    assert route_index < rule_index
    management_index = next(
        i for i, message in enumerate(events) if "management_cidrs [" in message
    )
    assert any("SSH management access:" in message for message in events[:management_index])
