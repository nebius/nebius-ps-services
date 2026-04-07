from __future__ import annotations

from pathlib import Path

import yaml

from nebius_cxcli.cli import _dynamic_provider_field_checks, _run_component_field_wizard
from nebius_cxcli.component_sources import ComponentDefault
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


def test_local_managed_modules_do_not_expose_internal_enabled_switches() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    managed_pg_dir = repo_root / "platform-infra" / "modules" / "managed-postgresql"
    sfs_dir = repo_root / "platform-infra" / "modules" / "sfs"
    object_storage_dir = repo_root / "platform-infra" / "modules" / "object-storage"

    managed_pg_specs = {item.name: item for item in module_variables(str(managed_pg_dir))}
    sfs_specs = {item.name: item for item in module_variables(str(sfs_dir))}
    object_storage_specs = {
        item.name: item for item in module_variables(str(object_storage_dir))
    }

    assert "enabled" not in managed_pg_specs
    assert "enabled" not in sfs_specs
    assert "enabled" not in object_storage_specs
    assert "buckets" not in object_storage_specs
    assert managed_pg_specs["name"].required is True
    assert sfs_specs["name"].required is True
    assert sfs_specs["size_gib"].required is True
    assert object_storage_specs["name"].required is True


def test_module_variable_discovery_parses_multiline_map_defaults(tmp_path: Path) -> None:
    module_dir = tmp_path / "demo-module"
    module_dir.mkdir(parents=True)
    (module_dir / "variables.tf").write_text(
        """
variable "gpu_driver_preset_map" {
  type = map(string)
  default = {
    "gpu-b200-sxm"   = "cuda12.8"
    "gpu-b200-sxm-a" = "cuda12.8"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    specs = {item.name: item for item in module_variables(str(module_dir))}
    assert specs["gpu_driver_preset_map"].type_hint == "map(string)"
    assert specs["gpu_driver_preset_map"].has_default is True
    assert specs["gpu_driver_preset_map"].default == {
        "gpu-b200-sxm": "cuda12.8",
        "gpu-b200-sxm-a": "cuda12.8",
    }


def test_mk8s_gpu_driver_preset_map_default_is_full_mapping() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    mk8s_dir = repo_root / "platform-infra" / "modules" / "mk8s"
    specs = {item.name: item for item in module_variables(str(mk8s_dir))}

    assert specs["gpu_driver_preset_map"].default == {
        "gpu-b200-sxm": "cuda12.8",
        "gpu-b200-sxm-a": "cuda12.8",
    }


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
    # mk8s cpu_nodes_platform is now declared in wizard_fields YAML (options.from),
    # not inferred.  Without wizard_fields on the entry, inference falls back to
    # compute_platforms.
    assert (
        "infra.components[0].inputs.cpu_nodes_platform",
        "compute_platforms",
        {"platform_prefix": "cpu-"},
    ) in checks


def test_wizard_prompts_required_and_optional_tf_variables_in_interactive_mode(
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
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
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
    assert "required_field" not in inputs
    assert "optional_field" not in inputs
    assert "infra.components[0].inputs.required_field" in prompted_paths
    assert "infra.components[0].inputs.optional_field" in prompted_paths
    assert prompt_type_hints["infra.components[0].inputs.required_field"] == "string"
    assert prompt_type_hints["infra.components[0].inputs.optional_field"] == "string"


def test_wizard_prompts_optional_complex_defaults_but_keeps_them_virtual(
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
                name="optional_map",
                required=False,
                type_hint="map(string)",
                has_default=True,
                default={},
            ),
            ModuleVariable(
                name="optional_list",
                required=False,
                type_hint="list(string)",
                has_default=True,
                default=[],
            ),
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._wizard_continue_phase",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )

    prompted_paths: list[str] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required
        prompted_paths.append(path_label)
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
    inputs = payload["infra"]["components"][0]["inputs"]
    assert "optional_map" not in inputs
    assert "optional_list" not in inputs
    assert prompted_paths == [
        "infra.components[0].inputs.required_field",
        "infra.components[0].inputs.optional_list",
        "infra.components[0].inputs.optional_map",
    ]


def test_wizard_leaves_optional_unset_field_absent_when_blank(
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

    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="optional_field", required=False, type_hint="string"),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "nebius_cxcli.cli._prompt_scalar_override",
        lambda _path_label, current, **_kwargs: (current, False),
    )

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
    inputs = payload["infra"]["components"][0]["inputs"]
    assert "optional_field" not in inputs


def test_wizard_prompts_literal_component_defaults_for_custom_module_fields(
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
        defaults=(
            ComponentDefault(target_path="inputs.cpu_nodes_count", value=2, kind="literal"),
        ),
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
                name="cpu_nodes_count",
                required=False,
                type_hint="number",
                has_default=True,
                default=None,
            ),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    prompted_values: dict[str, object] = {}

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required
        prompted_values[path_label] = current
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
    assert prompted_values["infra.components[0].inputs.cpu_nodes_count"] == 2
    payload = yaml.safe_load(updated_yaml)
    assert payload["infra"]["components"][0]["inputs"]["cpu_nodes_count"] == 2


def test_wizard_keeps_optional_provider_field_unset_without_reprompt(
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

    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="optional_field", required=False, type_hint="string"),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._provider_allowed_values_for_field",
        lambda **kwargs: (
            ({"allowed-value"}, ("fake-provider",))
            if kwargs.get("full_path_label", "").endswith(".optional_field")
            else (set(), ())
        ),
    )

    prompt_calls: list[str] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required
        prompt_calls.append(path_label)
        if len(prompt_calls) > 1:
            raise AssertionError("Optional skipped provider-backed field should not re-prompt.")
        return None, False

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
    assert prompt_calls == ["infra.components[0].inputs.optional_field"]
    payload = yaml.safe_load(updated_yaml)
    assert "optional_field" not in payload["infra"]["components"][0]["inputs"]


def test_wizard_skips_dependent_fields_when_enabled_toggle_is_false(
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
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )

    prompted_paths: list[str] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required
        prompted_paths.append(path_label)
        return False if path_label.endswith(".gpu_enabled") else current, False

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
    assert "infra.components[0].inputs.cluster_name" in prompted_paths
    assert "infra.components[0].inputs.gpu_enabled" in prompted_paths
    assert "infra.components[0].inputs.gpu_nodes_platform" not in prompted_paths


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
