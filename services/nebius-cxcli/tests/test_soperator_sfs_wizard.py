from __future__ import annotations

import copy
import sys
from io import StringIO
from types import SimpleNamespace

import pytest
import yaml
from rich.console import Console

from nebius_cxcli import cli
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.provider_options import OptionChoice, ProviderOptionLookup
from nebius_cxcli.soperator_sfs_wizard import (
    SFS_ROLES,
    prompt_sfs_filesystems,
    sfs_summary_rows,
)
from nebius_cxcli.wizard_profiles import BUILTIN_WIZARD_PROFILES

BACK = object()


def _inputs():
    return {
        "parent_id": "project-test",
        "type": "NETWORK_SSD",
        "filesystems": {
            role: {
                "name": f"cluster-{role}",
                "size_gib": 2048 if role == "jail" else 128,
                "block_size_kib": 4,
                "mount_tag": f"cluster-{role}",
                "forbid_deletion": False,
            }
            for role in SFS_ROLES
        },
    }


def _inventory():
    return [
        OptionChoice(
            f"filesystem-{role}",
            "provider label with inferred mount_tag=wrong-tag",
            metadata={"name": f"stored-{role}", "mount_tag": "wrong-tag"},
        )
        for role in SFS_ROLES
    ]


def _run(inputs, answer, *, lookup=None, errors=None):
    return prompt_sfs_filesystems(
        inputs,
        prompt=answer,
        filesystem_choices=lookup or (lambda: (_inventory(), None)),
        type_choices=lambda: [OptionChoice("NETWORK_SSD", "SSD")],
        report_error=(errors if errors is not None else []).append,
        backtrack=BACK,
    )


def _accept(field):
    if field.field == "existing_id":
        return f"filesystem-{field.role}", False
    return field.current, False


@pytest.mark.parametrize("existing", [set(), set(SFS_ROLES), {"controller-spool", "jail"}])
def test_roles_branch_independently_without_inactive_prompts(existing):
    inputs = _inputs()
    before = copy.deepcopy(inputs)
    seen = []
    lookups = []

    def answer(field):
        seen.append((field.role, field.field))
        if field.field == "source":
            return ("existing" if field.role in existing else "new"), False
        if field.field == "existing_id":
            assert all("wrong-tag" not in choice.label for choice in field.choices)
            assert field.choices
        return _accept(field)

    def lookup():
        lookups.append(True)
        return _inventory(), None

    result, stopped = _run(inputs, answer, lookup=lookup)
    assert not stopped
    assert inputs == before
    assert len(lookups) == len(existing)
    assert [role for role, field in seen if field == "source"] == list(SFS_ROLES)
    assert ((None, "type") in seen) == (len(existing) != 3)
    for role in SFS_ROLES:
        spec = result["filesystems"][role]
        assert spec["mount_tag"] == f"cluster-{role}"
        assert ((role, "name") in seen) == (role not in existing)
        assert ((role, "existing_id") in seen) == (role in existing)
        assert spec.get("existing_id") == (f"filesystem-{role}" if role in existing else None)
        # Retaining these fields preserves existing profile/derived-value semantics.
        assert spec["size_gib"] == before["filesystems"][role]["size_gib"]
    for _label, action, details, _tag in sfs_summary_rows(result):
        if action == "Use existing":
            assert details.startswith("filesystem-")
            assert "GiB" not in details and "NETWORK_SSD" not in details


def test_existing_config_defaults_to_reuse_and_new_clears_id():
    inputs = _inputs()
    inputs["filesystems"]["accounting"]["existing_id"] = "filesystem-accounting"
    seen = []

    def answer(field):
        seen.append(field)
        if field.field == "source" and field.role == "accounting":
            assert field.current == "existing"
            return "new", False
        return _accept(field)

    result, stopped = _run(inputs, answer, lookup=lambda: pytest.fail("new must not look up IDs"))
    assert not stopped
    assert "existing_id" not in result["filesystems"]["accounting"]


def test_mode_switch_and_backtracking_keep_creation_answers_and_visible_history():
    inputs = _inputs()
    script = iter(
        [
            ("accounting", "source", "new"),
            ("accounting", "name", "custom-accounting"),
            ("accounting", "size_gib", BACK),
            ("accounting", "name", BACK),
            ("accounting", "source", "existing"),
            ("accounting", "existing_id", "filesystem-accounting"),
            ("accounting", "mount_tag", BACK),
            ("accounting", "existing_id", BACK),
            ("accounting", "source", "new"),
            ("accounting", "name", "custom-accounting"),
        ]
    )
    visits = []

    def answer(field):
        visits.append((field.role, field.field))
        expected = next(script, None)
        if expected:
            assert (field.role, field.field) == expected[:2]
            if len(visits) == 10:
                assert field.current == "custom-accounting"
            return expected[2], False
        return _accept(field)

    result, stopped = _run(inputs, answer)
    assert not stopped
    assert result["filesystems"]["accounting"]["name"] == "custom-accounting"
    assert "existing_id" not in result["filesystems"]["accounting"]
    assert result["filesystems"]["jail"] == inputs["filesystems"]["jail"]


@pytest.mark.parametrize("error", [None, "Shared filesystem lookup failed: permission denied"])
def test_empty_or_failed_lookup_requires_explicit_new_selection(error):
    sources = iter(["existing", "new"])
    seen = []
    errors = []

    def answer(field):
        seen.append((field.role, field.field, field.current))
        if field.role == "accounting" and field.field == "source":
            return next(sources), False
        return _accept(field)

    result, stopped = _run(_inputs(), answer, lookup=lambda: ([], error), errors=errors)
    assert not stopped
    assert seen[:2] == [("accounting", "source", "new"), ("accounting", "source", "existing")]
    assert errors == [error or "No shared filesystems found in the current project."]
    assert "existing_id" not in result["filesystems"]["accounting"]


@pytest.mark.parametrize(
    "field,invalid",
    [
        ("existing_id", "filesystem-accounting"),
        ("mount_tag", "cluster-accounting"),
        ("mount_tag", "x" * 37),
        ("mount_tag", "é" * 19),
        ("size_gib", 0),
        ("block_size_kib", 3),
        ("name", ""),
    ],
)
def test_invalid_role_values_are_reprompted(field, invalid):
    inputs = _inputs()
    if field == "existing_id":
        for role in SFS_ROLES:
            inputs["filesystems"][role]["existing_id"] = f"filesystem-{role}"
    attempted = False
    errors = []

    def answer(item):
        nonlocal attempted
        if item.role == "controller-spool" and item.field == field and not attempted:
            attempted = True
            return invalid, False
        return _accept(item)

    result, stopped = _run(inputs, answer, errors=errors)
    assert not stopped and attempted
    assert len(errors) == 1
    assert result["filesystems"]["controller-spool"][field] != invalid


def test_profile_mount_tags_fit_virtiofs_limit_for_long_target_names():
    from nebius_cxcli.soperator_config_materialization import (
        _render_soperator_sfs_profile_filesystems,
        _soperator_nodesets_profiles,
    )

    _, profiles = _soperator_nodesets_profiles()
    for profile in profiles.values():
        for target in ("soperator-h200-upgrade", "cluster-" + "a" * 55):
            filesystems = _render_soperator_sfs_profile_filesystems(
                profile=profile, target_ref=target
            )
            assert len({row["mount_tag"] for row in filesystems.values()}) == len(filesystems)
            assert all(
                0 < len(row["mount_tag"].encode("utf-8")) <= 36 for row in filesystems.values()
            )


@pytest.mark.parametrize("tag", ["x" * 36, "x" * 37, "é" * 18, "é" * 19, "🙂" * 20])
def test_generated_mount_tags_fit_encoded_device_field(tag):
    from nebius_cxcli.filesystem_mount_tags import default_mount_tag, mount_tag_error

    rendered = default_mount_tag(tag)
    assert len(rendered.encode("utf-8")) <= 36
    assert mount_tag_error(rendered) is None
    assert default_mount_tag(tag) == rendered
    assert default_mount_tag(rendered) == rendered
    if len(tag.encode("utf-8")) <= 36:
        assert rendered == tag
    else:
        assert rendered != default_mount_tag(tag + "-other")


def test_sfs_summary_accepts_maximum_mount_tag_and_rejects_longer_tag():
    inputs = _inputs()
    inputs["filesystems"]["jail"]["mount_tag"] = "x" * 36
    assert sfs_summary_rows(inputs)
    inputs["filesystems"]["jail"]["mount_tag"] += "x"
    with pytest.raises(ValueError, match="36 UTF-8 bytes"):
        sfs_summary_rows(inputs)


def test_wizard_trims_mount_tag_before_validating_and_saving():
    def answer(field):
        if field.role == "jail" and field.field == "mount_tag":
            return " " + "x" * 36 + " ", False
        return _accept(field)

    errors = []
    result, stopped = _run(_inputs(), answer, errors=errors)
    assert not stopped and not errors
    assert result["filesystems"]["jail"]["mount_tag"] == "x" * 36


@pytest.mark.parametrize("cancel_field", ["source", "existing_id", "mount_tag", "type"])
def test_cancel_discards_draft_even_after_other_roles_completed(cancel_field):
    inputs = _inputs()
    before = copy.deepcopy(inputs)

    def answer(field):
        if field.role == "jail" and field.field == "source" and cancel_field != "source":
            return ("new" if cancel_field == "type" else "existing"), False
        if field.field == cancel_field and (field.role == "jail" or field.role is None):
            return None, True
        if field.field == "name":
            return "changed-" + field.role, False
        return _accept(field)

    result, stopped = _run(inputs, answer)
    assert stopped
    assert result == before == inputs


def test_back_from_first_step_leaves_component_unchanged():
    result, stopped = _run(_inputs(), lambda field: (BACK, False))
    assert result is BACK and not stopped


@pytest.fixture
def wizard(monkeypatch):
    monkeypatch.setattr(cli, "module_variables", lambda _source: ())
    monkeypatch.setattr(cli, "module_required_variables", lambda _source: ())
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli, "_wizard_continue_phase", lambda *a, **k: True)
    output = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=200, color_system=None))
    entry = ComponentEntry(
        id="sfs",
        scope="infra",
        config_path="infra.components[].inputs",
        description="SFS",
        wizard_fields=BUILTIN_WIZARD_PROFILES["sfs"],
    )

    def run(*, soperator_install=True, lookup=None, inputs=None):
        payload = {
            "client_info": {"nebius": {"project_id": "project-test"}},
            "infra": {
                "components": [
                    {
                        "id": "sfs",
                        "instance_id": "sfs",
                        "enabled": True,
                        "inputs": inputs or _inputs(),
                    }
                ]
            },
            "apps": {"charts": []},
        }
        updated, complete = cli._run_component_field_wizard(
            config_yaml=yaml.safe_dump(payload),
            selected_infra={"sfs"},
            selected_apps=set(),
            infra_entries=(entry,),
            app_entries=(),
            provider_lookup=lookup,
            soperator_install=soperator_install,
        )
        return (
            yaml.safe_load(updated)["infra"]["components"][0]["inputs"],
            complete,
            output.getvalue(),
        )

    return run


def test_real_wizard_all_new_uses_existing_primitives_and_never_looks_up_ids(monkeypatch, wizard):
    prompts = []

    def answer(text, default=None, **kwargs):
        prompts.append(text)
        return str(default)

    class Lookup(ProviderOptionLookup):
        def resolve(self, **kwargs):
            pytest.fail("all-new SFS wizard must not call a provider")

    monkeypatch.setattr(cli.typer, "prompt", answer)
    result, complete, output = wizard(lookup=Lookup())
    assert complete
    assert sum("Create new or use existing" in text for text in prompts) == 3
    assert sum("Type for new filesystems" in text for text in prompts) == 1
    assert all("Existing filesystem" not in text and "existing_id" not in text for text in prompts)
    assert "Shared filesystem plan" in output
    assert all("existing_id" not in row for row in result["filesystems"].values())


@pytest.mark.parametrize("quit_value", ["q", "qq", "eof"])
def test_real_text_navigation_returns_incomplete_without_draft(monkeypatch, wizard, quit_value):
    seen = []

    def answer(text, default=None, **kwargs):
        seen.append(text)
        if "Accounting / Create new or use existing" in text:
            if quit_value == "q" and len(seen) == 1:
                return "q"
            if quit_value == "eof":
                raise EOFError
            return "qq"
        return "qq"

    monkeypatch.setattr(cli.typer, "prompt", answer)
    result, complete, _ = wizard()
    assert not complete
    assert result["filesystems"] == _inputs()["filesystems"]


def test_skipping_customization_still_summarizes_defaults(monkeypatch, wizard):
    monkeypatch.setattr(cli, "_wizard_continue_phase", lambda *a, **k: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: pytest.fail("unexpected prompt"))
    result, complete, output = wizard()
    assert complete
    assert "Shared filesystem plan" in output and "2048 GiB" in output


def test_generic_sfs_keeps_generic_prompts(monkeypatch, wizard):
    seen = []

    def answer(text, default=None, **kwargs):
        seen.append(text)
        return "qq"

    monkeypatch.setattr(cli.typer, "prompt", answer)
    _, complete, output = wizard(soperator_install=False)
    assert not complete
    assert seen and "infra.components[0].inputs." in seen[0]
    assert "Create new or use existing" not in output


def test_single_existing_result_still_requires_a_prompt(monkeypatch, wizard):
    class Lookup(ProviderOptionLookup):
        def resolve(self, **kwargs):
            assert kwargs["args"] == {"project_id": "project-test"}
            return [_inventory()[0]]

    seen = []

    def answer(text, default=None, **kwargs):
        seen.append(text)
        if "Accounting / Create new or use existing" in text:
            return "existing"
        if "Accounting / Existing filesystem" in text:
            return "qq"
        pytest.fail(text)

    monkeypatch.setattr(cli.typer, "prompt", answer)
    _, complete, output = wizard(lookup=Lookup())
    assert not complete
    assert len(seen) == 2
    assert "wrong-tag" not in output and "skip / keep unset" not in output


@pytest.mark.parametrize("result", [None, cli._WIZARD_QUIT_CHOICE, cli._WIZARD_BACK_CHOICE])
def test_tty_existing_picker_navigation_is_required_and_atomic(monkeypatch, wizard, result):
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    seen = []

    def select(label, **kwargs):
        seen.append((label, kwargs))
        if len(seen) == 1:
            value = "existing"
        elif len(seen) == 2:
            value = result
            assert [choice["value"] for choice in kwargs["choices"]] == [
                item.value for item in _inventory()
            ]
        else:
            assert "Accounting / Create new or use existing" in label
            assert kwargs["default"] == "existing"
            value = cli._WIZARD_QUIT_CHOICE
        return SimpleNamespace(ask=lambda: value)

    monkeypatch.setitem(
        sys.modules,
        "questionary",
        SimpleNamespace(
            Choice=lambda **kwargs: kwargs,
            select=select,
        ),
    )
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: pytest.fail("TTY fell back to text"))

    class Lookup(ProviderOptionLookup):
        def resolve(self, **kwargs):
            return _inventory()

    updated, complete, _ = wizard(lookup=Lookup())
    assert not complete
    assert updated["filesystems"] == _inputs()["filesystems"]
    assert len(seen) == (3 if result == cli._WIZARD_BACK_CHOICE else 2)


@pytest.mark.parametrize("existing", [set(), {"accounting", "jail"}])
def test_saved_choices_survive_real_profile_normalization_and_runtime_conversion(existing):
    from nebius_cxcli.config_loader import normalize_runtime_config_payload
    from nebius_cxcli.config_model import to_runtime_payload

    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "node_group_defaults": {
                            "cpu": {"platform": "cpu-d3", "preset": "32vcpu-128gb"}
                        }
                    },
                },
                {"id": "sfs", "instance_id": "cluster1", "enabled": True, "inputs": {}},
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "profile": "nebius-cpu-v1",
                    "version": "4.1.8",
                    "values": {},
                }
            ]
        },
    }
    normalize_runtime_config_payload(payload)
    sfs = payload["infra"]["components"][1]

    def answer(field):
        if field.field == "source":
            return ("existing" if field.role in existing else "new"), False
        return _accept(field)

    updated, stopped = _run(sfs["inputs"], answer)
    assert not stopped
    sfs["inputs"] = updated
    expected = copy.deepcopy(updated["filesystems"])
    for _ in range(3):
        payload = yaml.safe_load(yaml.safe_dump(payload))
        normalize_runtime_config_payload(payload)
        runtime = to_runtime_payload(payload)
        actual = runtime["infra"]["components"][1]["inputs"]["filesystems"]
        assert actual == expected
        app_values = runtime["apps"]["charts"][0]["values"]
        for role in SFS_ROLES:
            assert app_values["sfs"]["filesystems"][role]["mount_tag"] == actual[role]["mount_tag"]
            assert actual[role].get("existing_id") == (
                f"filesystem-{role}" if role in existing else None
            )
