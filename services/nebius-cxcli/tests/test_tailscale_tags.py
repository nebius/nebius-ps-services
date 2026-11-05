"""Tag diagnostics and prompt retries preserve canonical router identities."""

import copy

import pytest

from nebius_cxcli import cli, tailscale_config, tailscale_wizard
from test_tailscale import row
from test_tailscale_wizard import answers


@pytest.mark.parametrize(
    "tags,message",
    [
        (None, "must be a list"),
        ("tag:router", "must be a list"),
        ([], "requires at least one"),
        (["router"], "must use tag:<name>"),
        (["tag:router_name"], "must use tag:<name>"),
        (["tag:1router"], "must use tag:<name>"),
        (["tag:"], "must use tag:<name>"),
        (["tag:router", None], "must use tag:<name>"),
        ([{}], "must use tag:<name>"),
        (["tag:router", "tag:router"], "duplicate entries"),
    ],
)
def test_runtime_tag_errors_identify_the_invalid_condition(tags, message):
    gateway = row()
    gateway["tailscale"]["tags"] = tags
    with pytest.raises(ValueError, match=message) as caught:
        tailscale_config.validate(gateway)
    assert "tailscale.tags" in str(caught.value)


def test_runtime_keeps_valid_tags_and_optional_infrastructure_tags():
    gateway = row()
    gateway["tailscale"]["tags"] = ["tag:Router", "tag:router-2"]
    assert tailscale_config.validate(gateway)["tags"] == ["tag:Router", "tag:router-2"]
    gateway["tailscale"].update(stage="infrastructure", tags=[])
    assert tailscale_config.validate(gateway)["tags"] == []
    del gateway["tailscale"]["tags"]
    tailscale_config.validate(gateway)


def test_tag_prompt_retries_syntax_and_duplicates_without_changing_saved_values(monkeypatch):
    prompts, messages = answers(
        monkeypatch, ["router", "tag:router, tag:router", "tag:router, tag:router-2"]
    )
    saved = ["tag:existing"]
    original = copy.deepcopy(saved)
    result, stop = tailscale_wizard.prompt_tags(saved, "tailscale.tags")
    assert not stop and result == ["tag:router", "tag:router-2"]
    assert saved == original
    assert len(prompts) == 3
    assert any("must use tag:<name>" in m for m in messages)
    assert any("duplicate entries" in m for m in messages)


@pytest.mark.parametrize("token", ["q", "qq"])
def test_tag_prompt_navigation_after_invalid_input(monkeypatch, token):
    answers(monkeypatch, ["router", token])
    saved = ["tag:existing"]
    result, stop = tailscale_wizard.prompt_tags(saved, "tailscale.tags")
    assert saved == ["tag:existing"]
    if token == "q":
        assert cli._wizard_backtrack_requested(result) and not stop
    else:
        assert stop


def test_tag_prompt_retries_invalid_saved_default(monkeypatch):
    prompts, _ = answers(monkeypatch, ["", "tag:router"])
    saved = ["router"]
    result, stop = tailscale_wizard.prompt_tags(saved, "tailscale.tags")
    assert not stop and result == ["tag:router"]
    assert saved == ["router"] and len(prompts) == 2


def test_tag_diagnostic_does_not_echo_rejected_input():
    gateway = row()
    gateway["tailscale"]["tags"] = ["sensitive-invalid-value"]
    with pytest.raises(ValueError) as caught:
        tailscale_config.validate(gateway)
    assert "sensitive-invalid-value" not in str(caught.value)
