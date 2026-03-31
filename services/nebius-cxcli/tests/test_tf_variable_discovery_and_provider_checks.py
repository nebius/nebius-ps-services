from __future__ import annotations

from pathlib import Path

import yaml

from nebius_cxcli.cli import _dynamic_provider_field_checks, _run_component_field_wizard
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.runtime_introspection import (
    ModuleVariable,
    module_required_variables,
    module_variable_names,
    module_variables,
)


def test_module_variable_discovery_includes_optional_and_required(tmp_path: Path) -> None:
    module_dir = tmp_path / "demo-module"
    module_dir.mkdir(parents=True)
    (module_dir / "variables.tf").write_text(
        """
variable "required_field" {
  type = string
}

variable "optional_field" {
  type    = string
  default = "demo"
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    assert module_variable_names(str(module_dir)) == ("optional_field", "required_field")
    assert module_required_variables(str(module_dir)) == ("required_field",)
    specs = {item.name: item for item in module_variables(str(module_dir))}
    assert specs["required_field"].type_hint == "string"
    assert specs["optional_field"].has_default is True
    assert specs["optional_field"].default == "demo"


def test_dynamic_provider_checks_cover_custom_tf_module_fields() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": {"cpu_nodes_platform": "cpu-d3"},
                }
            ]
        }
    }

    checks = _dynamic_provider_field_checks(payload=payload, infra_entries=())
    assert (
        "infra.components[0].inputs.cpu_nodes_platform",
        "mk8s_compatible_platforms",
        {"platform_prefix": "cpu-"},
    ) in checks


def test_wizard_prompts_required_tf_variables_only_by_default(
    monkeypatch,
) -> None:
    config_yaml = yaml.safe_dump(
        {
            "version": "v1",
            "client_info": {
                "client_name": "demo",
                "nebius": {
                    "tenant_id": "tenant-1",
                    "project_id": "project-1",
                    "region_id": "us-central1",
                },
                "notifications": {"email_enabled": True, "email": None},
            },
            "infra": {
                "components": [
                    {
                        "id": "mk8s",
                        "enabled": True,
                        "source": "../../platform-infra/modules/mk8s",
                        "inputs": {},
                    }
                ],
            },
            "apps": {"charts": []},
        },
        sort_keys=False,
    )

    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="Managed Kubernetes",
        origin="custom",
        source="../../platform-infra/modules/mk8s",
    )

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("required_field",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="required_field", required=True, type_hint="string"),
            ModuleVariable(
                name="optional_field",
                required=False,
                type_hint="string",
                has_default=True,
                default="demo",
            ),
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._wizard_continue_phase",
        lambda *_args, **_kwargs: True,
    )

    prompted_paths: list[str] = []
    prompt_type_hints: dict[str, str | None] = {}

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
    ) -> tuple[object, bool]:
        _ = choices, required
        prompted_paths.append(path_label)
        prompt_type_hints[path_label] = type_hint
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    payload = yaml.safe_load(updated_yaml)
    assert isinstance(payload, dict)

    components = payload["infra"]["components"]
    assert isinstance(components, list)
    inputs = components[0]["inputs"]
    assert inputs.get("required_field") == ""
    assert "optional_field" not in inputs
    assert "infra.components[0].inputs.required_field" in prompted_paths
    assert "infra.components[0].inputs.optional_field" not in prompted_paths
    assert prompt_type_hints["infra.components[0].inputs.required_field"] == "string"


def test_wizard_prompts_dependent_fields_when_enabled_toggle_is_true(
    monkeypatch,
) -> None:
    config_yaml = yaml.safe_dump(
        {
            "version": "v1",
            "client_info": {
                "client_name": "demo",
                "nebius": {
                    "tenant_id": "tenant-1",
                    "project_id": "project-1",
                    "region_id": "us-central1",
                },
                "notifications": {"email_enabled": True, "email": None},
            },
            "infra": {
                "components": [
                    {
                        "id": "mk8s",
                        "enabled": True,
                        "source": "../../platform-infra/modules/mk8s",
                        "inputs": {},
                    }
                ],
            },
            "apps": {"charts": []},
        },
        sort_keys=False,
    )
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="Managed Kubernetes",
        origin="custom",
        source="../../platform-infra/modules/mk8s",
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster_name",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="cluster_name", required=True, type_hint="string"),
            ModuleVariable(
                name="gpu_enabled",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
            ModuleVariable(
                name="gpu_nodes_platform",
                required=False,
                type_hint="string",
                has_default=True,
                default="",
            ),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    prompted_paths: list[str] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required
        prompted_paths.append(path_label)
        if path_label.endswith(".gpu_enabled"):
            return True, False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    _updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert "infra.components[0].inputs.gpu_enabled" in prompted_paths
    assert "infra.components[0].inputs.gpu_nodes_platform" in prompted_paths
