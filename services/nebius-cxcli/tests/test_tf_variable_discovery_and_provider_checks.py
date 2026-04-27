from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

import nebius_cxcli.cli as cli
from nebius_cxcli.cli import (
    _dynamic_provider_field_checks,
    _materialize_singleton_provider_defaults,
    _materialize_vm_image_defaults,
    _run_component_field_wizard,
)
from nebius_cxcli.component_sources import ComponentDefault
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.provider_options import OptionChoice
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
    object_storage_specs = {item.name: item for item in module_variables(str(object_storage_dir))}

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


def test_mk8s_gpu_module_uses_explicit_stack_source_contract() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    mk8s_dir = repo_root / "platform-infra" / "modules" / "mk8s"
    specs = {item.name: item for item in module_variables(str(mk8s_dir))}

    assert specs["gpu_stack_source"].default == "nebius_image"
    assert specs["cpu_nodes_os"].required is False
    assert specs["gpu_nodes_os"].required is False
    assert "gpu_driver_preset_map" not in specs
    assert "gpu_default_drivers_preset" not in specs
    assert "mig_strategy" not in specs
    assert "mig_parted_config" not in specs


def test_vm_module_requires_explicit_boot_image_source() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    vm_dir = repo_root / "platform-infra" / "modules" / "vm"
    specs = {item.name: item for item in module_variables(str(vm_dir))}

    assert specs["source_image_family"].has_default is True
    assert specs["source_image_family"].default is None


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

    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="Managed Kubernetes",
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.cpu_nodes_platform": {
                "options": {
                    "from": "mk8s_compatible_platforms",
                    "args": {"platform_prefix": "cpu-"},
                }
            }
        },
    )

    checks = _dynamic_provider_field_checks(payload=payload, infra_entries=(entry,))
    assert checks == (
        (
            "infra.components[0].inputs.cpu_nodes_platform",
            "mk8s_compatible_platforms",
            {"platform_prefix": "cpu-"},
        ),
    )


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


def test_wizard_declared_infra_field_does_not_warn_when_input_key_is_not_seeded(
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
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.cpu_nodes_platform": {
                "options": {
                    "from": "mk8s_compatible_platforms",
                    "args": {"platform_prefix": "cpu-"},
                }
            }
        },
    )

    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="cpu_nodes_platform", required=False, type_hint="string"),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    rendered_messages: list[str] = []
    prompted_paths: list[str] = []

    monkeypatch.setattr(
        "nebius_cxcli.cli.console.print",
        lambda *args, **_kwargs: rendered_messages.append(" ".join(str(arg) for arg in args)),
    )

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
        return "cpu-d3", False

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
    assert prompted_paths.count("infra.components[0].inputs.cpu_nodes_platform") == 1
    assert not any("Skipping wizard field" in message for message in rendered_messages)
    payload = yaml.safe_load(updated_yaml)
    assert payload["infra"]["components"][0]["inputs"]["cpu_nodes_platform"] == "cpu-d3"


def test_wizard_declared_nested_app_value_path_is_prompted_and_created(
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
            "infra": {"components": []},
            "apps": {
                "charts": [
                    {
                        "id": "demo-app",
                        "instance_id": "demo-app",
                        "enabled": True,
                        "repo": "oci://docker.io/example/demo-app",
                        "version": "1.0.0",
                        "namespace": "demo",
                        "release-name": "demo-app",
                        "values": {},
                    }
                ]
            },
        },
        sort_keys=False,
    )

    entry = ComponentEntry(
        id="demo-app",
        scope="apps",
        config_path="apps.platform.demo-app",
        description="Demo app",
        chart_name="demo-app",
        chart_repo="oci://docker.io/example/demo-app",
        default_namespace="demo",
        default_release_name="demo-app",
        wizard_fields={
            "values.image.tag": {},
        },
    )

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    rendered_messages: list[str] = []
    prompted_paths: list[str] = []

    monkeypatch.setattr(
        "nebius_cxcli.cli.console.print",
        lambda *args, **_kwargs: rendered_messages.append(" ".join(str(arg) for arg in args)),
    )

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
        return "1.2.3", False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra=set(),
        selected_apps={"demo-app"},
        infra_entries=(),
        app_entries=(entry,),
        provider_lookup=None,
    )

    assert completed is True
    assert prompted_paths.count("apps.charts[0].values.image.tag") == 1
    assert not any("Skipping wizard field" in message for message in rendered_messages)
    payload = yaml.safe_load(updated_yaml)
    assert payload["apps"]["charts"][0]["values"] == {"image": {"tag": "1.2.3"}}


def test_wizard_declared_target_observability_path_is_prompted(
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
                        "instance_id": "mk8s",
                        "enabled": True,
                        "inputs": {},
                    }
                ]
            },
            "apps": {"charts": []},
        },
        sort_keys=False,
    )

    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.components.mk8s",
        description="MK8s",
        wizard_fields={
            "deploy.targets[].observability.enabled": {},
        },
    )

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    rendered_messages: list[str] = []
    prompted_paths: list[str] = []

    monkeypatch.setattr(
        "nebius_cxcli.cli.console.print",
        lambda *args, **_kwargs: rendered_messages.append(" ".join(str(arg) for arg in args)),
    )

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
        return True, False

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
    assert prompted_paths.count("deploy.targets[0].observability.enabled") == 1
    assert not any("Skipping wizard field" in message for message in rendered_messages)
    payload = yaml.safe_load(updated_yaml)
    assert payload["deploy"]["targets"][0]["observability"]["enabled"] is True


def test_wizard_prints_section_banners_and_selected_values(monkeypatch) -> None:
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
                        "instance_id": "mk8s",
                        "enabled": True,
                        "source": "../../platform-infra/modules/mk8s",
                        "inputs": {},
                    }
                ],
            },
            "apps": {
                "charts": [
                    {
                        "id": "demo-app",
                        "instance_id": "mk8s",
                        "enabled": True,
                        "target_ref": "mk8s",
                        "repo": "oci://docker.io/example/demo-app",
                        "version": "1.0.0",
                        "namespace": "demo",
                        "release-name": "demo-app",
                        "values": {},
                    }
                ]
            },
        },
        sort_keys=False,
    )

    infra_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="Managed Kubernetes",
        source="../../platform-infra/modules/mk8s",
    )
    app_entry = ComponentEntry(
        id="demo-app",
        scope="apps",
        config_path="apps.demo-app",
        description="Demo app",
        chart_name="demo-app",
        chart_repo="oci://docker.io/example/demo-app",
        default_namespace="demo",
        default_release_name="demo-app",
        wizard_fields={"values.image.tag": {}},
    )

    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="required_field", required=True, type_hint="string"),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})

    rendered_messages: list[str] = []
    monkeypatch.setattr(
        "nebius_cxcli.cli.console.print",
        lambda *args, **_kwargs: rendered_messages.append(" ".join(str(arg) for arg in args)),
    )

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required
        if path_label == "infra.components[0].inputs.required_field":
            return "infra-value", False
        if path_label == "apps.charts[0].values.image.tag":
            return "1.2.3", False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    _updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s"},
        selected_apps={"demo-app@mk8s"},
        infra_entries=(infra_entry,),
        app_entries=(app_entry,),
        provider_lookup=None,
    )

    assert completed is True
    assert any("--- Infra wizard section ---" in message for message in rendered_messages)
    assert any("--- Apps wizard section ---" in message for message in rendered_messages)
    assert any(
        "Selected infra.components[0].inputs.required_field = infra-value" in message
        for message in rendered_messages
    )
    assert any(
        "Selected apps.charts[0].values.image.tag = 1.2.3" in message
        for message in rendered_messages
    )


def test_vm_preemptible_wizard_sets_required_recovery_policy_and_prompts_priority(
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
                        "id": "vm",
                        "instance_id": "vm",
                        "enabled": True,
                        "source": "../../platform-infra/modules/vm",
                        "inputs": {"platform": "gpu-h100-sxm"},
                    }
                ],
            },
            "apps": {"charts": []},
        },
        sort_keys=False,
    )

    entry = ComponentEntry(
        id="vm",
        scope="infra",
        config_path="infra.vm",
        description="Compute VM",
        source="../../platform-infra/modules/vm",
        wizard_fields={"inputs.recovery_policy": {"prompt": False}},
    )

    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="platform", required=False, type_hint="string"),
            ModuleVariable(
                name="preemptible_enabled",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
            ModuleVariable(
                name="preemptible_priority",
                required=False,
                type_hint="number",
                has_default=True,
                default=3,
            ),
            ModuleVariable(
                name="recovery_policy",
                required=False,
                type_hint="string",
                has_default=True,
                default="RECOVER",
            ),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    prompted_paths: list[str] = []
    rendered_messages: list[str] = []
    monkeypatch.setattr(
        "nebius_cxcli.cli.console.print",
        lambda *args, **_kwargs: rendered_messages.append(" ".join(str(arg) for arg in args)),
    )

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
        if path_label == "infra.components[0].inputs.preemptible_enabled":
            return True, False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"vm"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    payload = yaml.safe_load(updated_yaml)
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["preemptible_enabled"] is True
    assert inputs["recovery_policy"] == "FAIL"
    assert "infra.components[0].inputs.recovery_policy" not in prompted_paths
    assert "infra.components[0].inputs.preemptible_priority" in prompted_paths
    assert any("Adjusted VM preemptible settings:" in message for message in rendered_messages)


def test_vm_preemptible_wizard_hides_preemptible_fields_for_cpu_platform(
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
                        "id": "vm",
                        "instance_id": "vm",
                        "enabled": True,
                        "source": "../../platform-infra/modules/vm",
                        "inputs": {"platform": "cpu-d3"},
                    }
                ],
            },
            "apps": {"charts": []},
        },
        sort_keys=False,
    )

    entry = ComponentEntry(
        id="vm",
        scope="infra",
        config_path="infra.vm",
        description="Compute VM",
        source="../../platform-infra/modules/vm",
    )

    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="platform", required=False, type_hint="string"),
            ModuleVariable(
                name="preemptible_enabled",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
            ModuleVariable(
                name="preemptible_priority",
                required=False,
                type_hint="number",
                has_default=True,
                default=3,
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
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    _updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"vm"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert "infra.components[0].inputs.preemptible_enabled" not in prompted_paths
    assert "infra.components[0].inputs.preemptible_priority" not in prompted_paths


def test_wizard_prompts_vm_observability_without_duplicate_root_prompt(monkeypatch) -> None:
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
                        "instance_id": "mk8s",
                        "enabled": True,
                        "inputs": {},
                    },
                    {
                        "id": "vm",
                        "instance_id": "vm",
                        "enabled": True,
                        "inputs": {},
                    },
                ]
            },
            "apps": {"charts": []},
        },
        sort_keys=False,
    )

    mk8s_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.components.mk8s",
        description="MK8s",
        wizard_fields={
            "deploy.targets[].observability.enabled": {},
        },
    )
    vm_entry = ComponentEntry(
        id="vm",
        scope="infra",
        config_path="infra.components.vm",
        description="VM",
        wizard_fields={
            "deploy.observability.enabled": {},
            "deploy.observability.vm.logs.enabled": {},
        },
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
        _ = current, choices, type_hint, required
        prompted_paths.append(path_label)
        return True, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s", "vm"},
        selected_apps=set(),
        infra_entries=(mk8s_entry, vm_entry),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert prompted_paths.count("deploy.targets[0].observability.enabled") == 1
    assert prompted_paths.count("deploy.observability.enabled") == 1
    assert prompted_paths.count("deploy.observability.vm.logs.enabled") == 1
    payload = yaml.safe_load(updated_yaml)
    assert payload["deploy"]["targets"][0]["observability"]["enabled"] is True
    assert payload["deploy"]["observability"]["enabled"] is True
    assert payload["deploy"]["observability"]["vm"]["logs"]["enabled"] is True


def test_vm_service_account_prompt_only_appears_when_standalone_collector_enabled(
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
            "deploy": {
                "observability": {
                    "enabled": True,
                    "vm": {
                        "collector": {
                            "enabled": True,
                        }
                    },
                },
            },
            "infra": {
                "components": [
                    {
                        "id": "vm",
                        "instance_id": "vm",
                        "enabled": True,
                        "inputs": {},
                    }
                ]
            },
            "apps": {"charts": []},
        },
        sort_keys=False,
    )

    vm_entry = ComponentEntry(
        id="vm",
        scope="infra",
        config_path="infra.components.vm",
        description="VM",
        wizard_fields={
            "deploy.observability.enabled": {},
            "deploy.observability.vm.collector.enabled": {},
            "inputs.service_account_id": {},
        },
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
        _ = current, choices, type_hint, required
        prompted_paths.append(path_label)
        if path_label.endswith(".inputs.service_account_id"):
            return "serviceaccount-1", False
        return True, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"vm"},
        selected_apps=set(),
        infra_entries=(vm_entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert any(path.endswith(".inputs.service_account_id") for path in prompted_paths)
    payload = yaml.safe_load(updated_yaml)
    assert payload["infra"]["components"][0]["inputs"]["service_account_id"] == "serviceaccount-1"


def test_wizard_q_revisits_previous_nested_app_value_prompt(monkeypatch) -> None:
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
            "infra": {"components": []},
            "apps": {
                "charts": [
                    {
                        "id": "demo-app",
                        "instance_id": "demo-app",
                        "enabled": True,
                        "repo": "oci://docker.io/example/demo-app",
                        "version": "1.0.0",
                        "namespace": "demo",
                        "release-name": "demo-app",
                        "values": {
                            "maintenance-operator-chart": {
                                "operator": {
                                    "admissionController": {
                                        "certificates": {
                                            "certManager": {
                                                "enable": False,
                                                "issuerRef": "issuer-a",
                                            },
                                            "selfSigned": {
                                                "enable": True,
                                            },
                                        }
                                    }
                                }
                            }
                        },
                    }
                ]
            },
        },
        sort_keys=False,
    )

    entry = ComponentEntry(
        id="demo-app",
        scope="apps",
        config_path="apps.platform.demo-app",
        description="Demo app",
        chart_name="demo-app",
        chart_repo="oci://docker.io/example/demo-app",
        default_namespace="demo",
        default_release_name="demo-app",
    )

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    prompted_paths: list[str] = []
    prompt_counts: dict[str, int] = {}

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
        prompt_counts[path_label] = prompt_counts.get(path_label, 0) + 1
        if path_label.endswith(".certManager.enable") and prompt_counts[path_label] == 1:
            return cli._WIZARD_BACKTRACK, False
        if path_label.endswith(".selfSigned.enable"):
            return False, False
        if path_label.endswith(".certManager.enable"):
            return False, False
        if path_label.endswith(".certManager.issuerRef"):
            return "issuer-b", False
        return "demo", False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra=set(),
        selected_apps={"demo-app"},
        infra_entries=(),
        app_entries=(entry,),
        provider_lookup=None,
    )

    assert completed is True
    issuer_ref_path = (
        "apps.charts[0].values.maintenance-operator-chart.operator.admissionController."
        "certificates.certManager.issuerRef"
    )
    release_name_path = "apps.charts[0].release-name"
    assert issuer_ref_path in prompted_paths
    assert prompted_paths.count(release_name_path) == 2
    assert (
        "apps.charts[0].values.maintenance-operator-chart.operator.admissionController."
        "certificates.selfSigned.enable"
    ) in prompted_paths
    payload = yaml.safe_load(updated_yaml)
    certs = payload["apps"]["charts"][0]["values"]["maintenance-operator-chart"]["operator"][
        "admissionController"
    ]["certificates"]
    assert certs["certManager"] == {
        "enable": False,
        "issuerRef": "issuer-b",
    }
    assert certs["selfSigned"]["enable"] is False


def test_wizard_q_revisits_previous_flat_module_prompt(monkeypatch) -> None:
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
                "notifications": {"email_enabled": False, "email": None},
            },
            "infra": {
                "components": [
                    {
                        "id": "demo-infra",
                        "enabled": True,
                        "source": "../../platform-infra/modules/demo-infra",
                        "inputs": {},
                    }
                ]
            },
            "apps": {"charts": []},
        },
        sort_keys=False,
    )

    entry = ComponentEntry(
        id="demo-infra",
        scope="infra",
        config_path="infra.demo-infra",
        description="Demo infra",
        source="../../platform-infra/modules/demo-infra",
    )

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster_name",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
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
                name="cpu_nodes_boot_disk_type",
                required=False,
                type_hint="string",
                has_default=True,
                default="NETWORK_SSD",
            ),
            ModuleVariable(
                name="cpu_nodes_boot_disk_size_gib",
                required=False,
                type_hint="number",
                has_default=True,
                default=93,
            ),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    prompted_paths: list[str] = []
    prompt_counts: dict[str, int] = {}

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
        prompt_counts[path_label] = prompt_counts.get(path_label, 0) + 1
        if path_label.endswith(".cluster_name"):
            return "cluster1", False
        if path_label.endswith(".gpu_enabled"):
            return prompt_counts[path_label] > 1, False
        if path_label.endswith(".cpu_nodes_boot_disk_type"):
            if prompt_counts[path_label] == 1:
                return cli._WIZARD_BACKTRACK, False
            return "NETWORK_SSD", False
        if path_label.endswith(".cpu_nodes_boot_disk_size_gib"):
            return 93, False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"demo-infra"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert prompted_paths == [
        "infra.components[0].inputs.cluster_name",
        "infra.components[0].inputs.gpu_enabled",
        "infra.components[0].inputs.cpu_nodes_boot_disk_type",
        "infra.components[0].inputs.gpu_enabled",
        "infra.components[0].inputs.cpu_nodes_boot_disk_type",
        "infra.components[0].inputs.cpu_nodes_boot_disk_size_gib",
    ]
    payload = yaml.safe_load(updated_yaml)
    assert payload["infra"]["components"][0]["inputs"]["gpu_enabled"] is True


def test_wizard_auto_selects_single_provider_option_for_optional_field(
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
                        "inputs": {
                            "gpu_nodes_platform": "gpu-b200-sxm",
                        },
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
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.gpu_stack_preset": {
                "options": {
                    "from": "mk8s_gpu_stack_presets",
                    "args": {"platform_path": "inputs.gpu_nodes_platform"},
                    "auto_select_single": True,
                }
            }
        },
    )

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: (),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="gpu_stack_preset", required=False, type_hint="string"),
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

    prompted: list[tuple[str, object]] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required
        prompted.append((path_label, current))
        return current, False

    class _Lookup:
        def resolve(self, *, provider, args, payload, field_path):
            _ = args, payload
            if provider == "mk8s_gpu_stack_presets" and field_path.endswith(".gpu_stack_preset"):
                return [OptionChoice(value="cuda13.0", label="cuda13.0")]
            return []

        def last_error(self):
            return None

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_Lookup(),
    )

    assert completed is True
    assert prompted == [("infra.components[0].inputs.gpu_stack_preset", "cuda13.0")]
    payload = yaml.safe_load(updated_yaml)
    assert payload["infra"]["components"][0]["inputs"]["gpu_stack_preset"] == "cuda13.0"


def test_materialize_singleton_provider_defaults_sets_missing_single_choice_field() -> None:
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "us-central1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {
                        "gpu_nodes_platform": "gpu-b200-sxm",
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="Managed Kubernetes",
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.gpu_stack_preset": {
                "options": {
                    "from": "mk8s_gpu_stack_presets",
                    "args": {"platform_path": "inputs.gpu_nodes_platform"},
                    "auto_select_single": True,
                }
            }
        },
    )

    class _Lookup:
        def resolve(self, *, provider, args, payload, field_path):
            _ = args, payload
            if provider == "mk8s_gpu_stack_presets" and field_path.endswith(".gpu_stack_preset"):
                return [OptionChoice(value="cuda13.0", label="cuda13.0")]
            return []

        def last_error(self):
            return None

    _materialize_singleton_provider_defaults(
        payload=payload,
        selected_infra={"mk8s"},
        infra_entries=(entry,),
        provider_lookup=_Lookup(),
    )

    assert payload["infra"]["components"][0]["inputs"]["gpu_stack_preset"] == "cuda13.0"


def test_materialize_singleton_provider_defaults_sets_clusterable_gpu_preset() -> None:
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "us-central1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {
                        "gpu_nodes_platform": "gpu-b200-sxm",
                        "infiniband_fabric": "us-central1-b",
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="Managed Kubernetes",
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.gpu_nodes_preset": {
                "options": {
                    "from": "compute_platform_presets",
                    "args": {
                        "platform_path": "inputs.gpu_nodes_platform",
                        "gpu_cluster_required_path": "inputs.infiniband_fabric",
                    },
                    "auto_select_single": True,
                }
            }
        },
    )

    class _Lookup:
        def resolve(self, *, provider, args, payload, field_path):
            _ = args, payload
            if provider == "compute_platform_presets" and field_path.endswith(".gpu_nodes_preset"):
                return [OptionChoice(value="8gpu-160vcpu-1792gb", label="8gpu-160vcpu-1792gb")]
            return []

        def last_error(self):
            return None

    _materialize_singleton_provider_defaults(
        payload=payload,
        selected_infra={"mk8s"},
        infra_entries=(entry,),
        provider_lookup=_Lookup(),
    )

    assert payload["infra"]["components"][0]["inputs"]["gpu_nodes_preset"] == "8gpu-160vcpu-1792gb"


def test_materialize_vm_image_defaults_sets_first_live_image_family() -> None:
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
                    "id": "vm",
                    "instance_id": "vm",
                    "enabled": True,
                    "inputs": {
                        "platform": "gpu-h100-sxm",
                        "preset": "1gpu-16vcpu-200gb",
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    entry = ComponentEntry(
        id="vm",
        scope="infra",
        config_path="infra.vm",
        description="Compute VM",
        source="../../platform-infra/modules/vm",
        validation_profile="vm_instance",
        wizard_fields={
            "inputs.source_image_family": {
                "options": {
                    "from": "compute_public_image_families",
                    "args": {"platform_path": "inputs.platform"},
                    "auto_select_first": True,
                }
            }
        },
    )

    class _Lookup:
        def resolve(self, *, provider, args, payload, field_path):
            _ = args, payload
            if provider == "compute_public_image_families" and field_path.endswith(
                ".source_image_family"
            ):
                return [
                    OptionChoice(
                        value="ubuntu24.04-cuda13.0",
                        label="ubuntu24.04-cuda13.0  (recommended)",
                    )
                ]
            return []

        def last_error(self):
            return None

    _materialize_vm_image_defaults(
        payload=payload,
        selected_infra={"vm"},
        infra_entries=(entry,),
        provider_lookup=_Lookup(),
    )

    assert payload["infra"]["components"][0]["inputs"]["source_image_family"] == (
        "ubuntu24.04-cuda13.0"
    )


def test_wizard_skips_empty_top_level_app_values_prompt_without_known_leaf_fields(
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
            "infra": {"components": []},
            "apps": {
                "charts": [
                    {
                        "id": "demo-app",
                        "instance_id": "demo-app",
                        "enabled": True,
                        "repo": "oci://docker.io/example/demo-app",
                        "version": "1.0.0",
                        "namespace": "demo",
                        "release-name": "demo-app",
                        "values": {},
                    }
                ]
            },
        },
        sort_keys=False,
    )

    entry = ComponentEntry(
        id="demo-app",
        scope="apps",
        config_path="apps.platform.demo-app",
        description="Demo app",
        chart_name="demo-app",
        chart_repo="oci://docker.io/example/demo-app",
        default_namespace="demo",
        default_release_name="demo-app",
    )

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("nebius_cxcli.cli._app_chart_default_values", lambda **_kwargs: {})

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
        return "demo", False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra=set(),
        selected_apps={"demo-app"},
        infra_entries=(),
        app_entries=(entry,),
        provider_lookup=None,
    )

    assert completed is True
    assert prompted_paths == [
        "apps.charts[0].namespace",
        "apps.charts[0].release-name",
    ]
    payload = yaml.safe_load(updated_yaml)
    assert payload["apps"]["charts"][0]["values"] == {}


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
        source="../../platform-infra/modules/mk8s",
        defaults=(ComponentDefault(target_path="inputs.cpu_nodes_count", value=2, kind="literal"),),
    )

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster_name",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
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


def test_wizard_skips_optional_module_field_marked_prompt_false(
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
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.mk8s_cluster_overrides": {
                "prompt": False,
            }
        },
    )

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster_name",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="cluster_name", required=True, type_hint="string"),
            ModuleVariable(
                name="mk8s_cluster_overrides",
                required=False,
                type_hint="map(any)",
                has_default=True,
                default={},
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
        _ = current, choices, type_hint, required
        prompted_paths.append(path_label)
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
    assert "infra.components[0].inputs.cluster_name" in prompted_paths
    assert "infra.components[0].inputs.mk8s_cluster_overrides" not in prompted_paths


def test_wizard_uses_declared_default_for_nested_helper_field_without_persisting_when_unchanged(
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
                        "id": "demo-module",
                        "enabled": True,
                        "source": "../../platform-infra/modules/demo-module",
                        "inputs": {},
                    }
                ],
            },
            "apps": {"charts": []},
        },
        sort_keys=False,
    )

    entry = ComponentEntry(
        id="demo-module",
        scope="infra",
        config_path="infra.demo-module",
        description="Demo module",
        source="../../platform-infra/modules/demo-module",
        wizard_fields={
            "inputs.demo_toggle_group.enabled": {
                "default": False,
            }
        },
    )

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster_name",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (ModuleVariable(name="cluster_name", required=True, type_hint="string"),),
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
        selected_infra={"demo-module"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert prompted_values["infra.components[0].inputs.demo_toggle_group.enabled"] is False
    payload = yaml.safe_load(updated_yaml)
    assert "demo_toggle_group" not in payload["infra"]["components"][0]["inputs"]


def test_wizard_skips_irrelevant_mk8s_gpu_validation_prompts_until_gpu_cluster_is_enabled(
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
                        "inputs": {
                            "gpu_enabled": True,
                        },
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
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "deploy.targets[].validations.mk8s_gpu.operator_readiness.enabled": {"default": True},
            "deploy.targets[].validations.mk8s_gpu.gpu_visibility.enabled": {"default": True},
            "deploy.targets[].validations.mk8s_gpu.gpu_visibility.max_nodes": {"default": 3},
            "deploy.targets[].validations.mk8s_gpu.nccl.enabled": {"default": True},
            "deploy.targets[].validations.mk8s_gpu.nccl.max_nodes": {"default": 8},
            "deploy.targets[].validations.mk8s_gpu.nccl.average_bus_bandwidth_threshold_gbps": {
                "default": 300
            },
            "deploy.targets[].validations.mk8s_gpu.health_checker.enabled": {"default": False},
        },
    )

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster_name",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="cluster_name", required=True, type_hint="string"),
            ModuleVariable(name="gpu_enabled", required=False, type_hint="bool"),
            ModuleVariable(name="infiniband_fabric", required=False, type_hint="string"),
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
    assert "deploy.targets[0].validations.mk8s_gpu.operator_readiness.enabled" in prompted_paths
    assert "deploy.targets[0].validations.mk8s_gpu.gpu_visibility.enabled" in prompted_paths
    assert "deploy.targets[0].validations.mk8s_gpu.gpu_visibility.max_nodes" in prompted_paths
    assert "deploy.targets[0].validations.mk8s_gpu.nccl.enabled" in prompted_paths
    assert "deploy.targets[0].validations.mk8s_gpu.nccl.max_nodes" in prompted_paths
    assert (
        "deploy.targets[0].validations.mk8s_gpu.nccl.average_bus_bandwidth_threshold_gbps"
        not in prompted_paths
    )
    assert "deploy.targets[0].validations.mk8s_gpu.health_checker.enabled" not in prompted_paths


def test_wizard_auto_enabled_mk8s_gpu_apps_are_prompted_in_same_pass(monkeypatch) -> None:
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
                        "instance_id": "mk8s",
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

    infra_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="Managed Kubernetes",
        source="../../platform-infra/modules/mk8s",
    )
    network_entry = ComponentEntry(
        id="nvidia-network-operator",
        scope="apps",
        config_path="apps.platform.nvidia-network-operator",
        description="Network operator",
        group="platform",
        source="oci://example.invalid/network-operator",
        version="1.0.0",
        default_namespace="nvidia-network-operator",
        default_release_name="network-operator",
    )
    gpu_entry = ComponentEntry(
        id="nvidia-gpu-operator",
        scope="apps",
        config_path="apps.platform.nvidia-gpu-operator",
        description="GPU operator",
        group="platform",
        source="oci://example.invalid/gpu-operator",
        version="1.0.0",
        default_namespace="nvidia-gpu-operator",
        default_release_name="gpu-operator",
    )

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster_name",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="cluster_name", required=True, type_hint="string"),
            ModuleVariable(name="gpu_enabled", required=False, type_hint="bool"),
            ModuleVariable(name="infiniband_fabric", required=False, type_hint="string"),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._app_chart_default_values", lambda **_kwargs: {})

    phase_prompts: list[tuple[str, bool]] = []

    def _capture_continue_phase(label: str, *, default: bool = True) -> bool:
        phase_prompts.append((label, default))
        return True

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)

    def _fake_gpu_selection(payload, *, selected_app_ids=None, app_entries=None):
        _ = selected_app_ids, app_entries
        inputs = payload["infra"]["components"][0]["inputs"]
        if inputs.get("gpu_enabled") and inputs.get("infiniband_fabric"):
            return SimpleNamespace(
                selected_app_ids=("nvidia-gpu-operator", "nvidia-network-operator"),
                auto_enabled_app_ids=("nvidia-gpu-operator", "nvidia-network-operator"),
                issues=(),
            )
        return SimpleNamespace(
            selected_app_ids=tuple(sorted(selected_app_ids or ())),
            auto_enabled_app_ids=(),
            issues=(),
        )

    monkeypatch.setattr("nebius_cxcli.cli.resolve_mk8s_gpu_app_selection", _fake_gpu_selection)

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required
        if path_label.endswith(".cluster_name"):
            return "cluster1", False
        if path_label.endswith(".gpu_enabled"):
            return True, False
        if path_label.endswith(".infiniband_fabric"):
            return "fabric-1", False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(infra_entry,),
        app_entries=(network_entry, gpu_entry),
        provider_lookup=None,
    )

    assert completed is True
    assert phase_prompts == [
        ("Configure 'mk8s' component fields now?", True),
        ("Configure 'nvidia-network-operator on mk8s' component fields now?", False),
        ("Configure 'nvidia-gpu-operator on mk8s' component fields now?", False),
    ]
    payload = yaml.safe_load(updated_yaml)
    assert [item["id"] for item in payload["apps"]["charts"]] == [
        "nvidia-network-operator",
        "nvidia-gpu-operator",
    ]


def test_wizard_skipping_one_auto_enabled_app_still_prompts_the_next_app(
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
                        "instance_id": "mk8s",
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

    infra_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="Managed Kubernetes",
        source="../../platform-infra/modules/mk8s",
    )
    network_entry = ComponentEntry(
        id="nvidia-network-operator",
        scope="apps",
        config_path="apps.platform.nvidia-network-operator",
        description="Network operator",
        group="platform",
        source="oci://example.invalid/network-operator",
        version="1.0.0",
        default_namespace="nvidia-network-operator",
        default_release_name="network-operator",
    )
    gpu_entry = ComponentEntry(
        id="nvidia-gpu-operator",
        scope="apps",
        config_path="apps.platform.nvidia-gpu-operator",
        description="GPU operator",
        group="platform",
        source="oci://example.invalid/gpu-operator",
        version="1.0.0",
        default_namespace="nvidia-gpu-operator",
        default_release_name="gpu-operator",
    )

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster_name",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="cluster_name", required=True, type_hint="string"),
            ModuleVariable(name="gpu_enabled", required=False, type_hint="bool"),
            ModuleVariable(name="infiniband_fabric", required=False, type_hint="string"),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._app_chart_default_values", lambda **_kwargs: {})

    phase_prompts: list[tuple[str, bool]] = []
    phase_answers = iter((True, False, True))

    def _capture_continue_phase(label: str, *, default: bool = True):
        phase_prompts.append((label, default))
        return next(phase_answers)

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)

    def _fake_gpu_selection(payload, *, selected_app_ids=None, app_entries=None):
        _ = selected_app_ids, app_entries
        inputs = payload["infra"]["components"][0]["inputs"]
        if inputs.get("gpu_enabled") and inputs.get("infiniband_fabric"):
            return SimpleNamespace(
                selected_app_ids=("nvidia-gpu-operator", "nvidia-network-operator"),
                auto_enabled_app_ids=("nvidia-gpu-operator", "nvidia-network-operator"),
                issues=(),
            )
        return SimpleNamespace(
            selected_app_ids=tuple(sorted(selected_app_ids or ())),
            auto_enabled_app_ids=(),
            issues=(),
        )

    monkeypatch.setattr("nebius_cxcli.cli.resolve_mk8s_gpu_app_selection", _fake_gpu_selection)

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required
        if path_label.endswith(".cluster_name"):
            return "cluster1", False
        if path_label.endswith(".gpu_enabled"):
            return True, False
        if path_label.endswith(".infiniband_fabric"):
            return "fabric-1", False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(infra_entry,),
        app_entries=(network_entry, gpu_entry),
        provider_lookup=None,
    )

    assert completed is True
    assert phase_prompts == [
        ("Configure 'mk8s' component fields now?", True),
        ("Configure 'nvidia-network-operator on mk8s' component fields now?", False),
        ("Configure 'nvidia-gpu-operator on mk8s' component fields now?", False),
    ]
    payload = yaml.safe_load(updated_yaml)
    assert [item["id"] for item in payload["apps"]["charts"]] == [
        "nvidia-network-operator",
        "nvidia-gpu-operator",
    ]


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
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.gpu_nodes_platform": {
                "options": {
                    "from": "mk8s_compatible_platforms",
                    "args": {"platform_prefix": "gpu-"},
                }
            },
            "inputs.gpu_nodes_preset": {
                "options": {
                    "from": "compute_platform_presets",
                    "args": {"platform_path": "inputs.gpu_nodes_platform"},
                }
            },
        },
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
    assert "infra.components[0].inputs.gpu_nodes_preset" not in prompted_paths


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
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.gpu_nodes_platform": {
                "options": {
                    "from": "mk8s_compatible_platforms",
                    "args": {"platform_prefix": "gpu-"},
                }
            },
            "inputs.gpu_nodes_preset": {
                "options": {
                    "from": "compute_platform_presets",
                    "args": {"platform_path": "inputs.gpu_nodes_platform"},
                }
            },
        },
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
    assert "infra.components[0].inputs.gpu_nodes_preset" not in prompted_paths


def test_wizard_prompts_gpu_preset_only_after_platform_is_selected(
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
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.gpu_nodes_platform": {
                "options": {
                    "from": "mk8s_compatible_platforms",
                    "args": {"platform_prefix": "gpu-"},
                }
            },
            "inputs.gpu_nodes_preset": {
                "options": {
                    "from": "compute_platform_presets",
                    "args": {"platform_path": "inputs.gpu_nodes_platform"},
                }
            },
        },
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
            ModuleVariable(
                name="gpu_nodes_preset",
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
        _ = current, choices, type_hint, required
        prompted_paths.append(path_label)
        if path_label.endswith(".gpu_enabled"):
            return True, False
        if path_label.endswith(".gpu_nodes_platform"):
            return "gpu-h100-sxm", False
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
    assert "infra.components[0].inputs.gpu_nodes_platform" in prompted_paths
    assert "infra.components[0].inputs.gpu_nodes_preset" in prompted_paths


def test_wizard_expands_mk8s_gpu_followup_prompts_immediately(
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
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.gpu_nodes_platform": {
                "options": {
                    "from": "mk8s_compatible_platforms",
                    "args": {"platform_prefix": "gpu-"},
                }
            },
            "inputs.gpu_nodes_preset": {
                "options": {
                    "from": "compute_platform_presets",
                    "args": {"platform_path": "inputs.gpu_nodes_platform"},
                }
            },
        },
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster_name",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
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
                name="gpu_node_groups",
                required=False,
                type_hint="number",
                has_default=True,
                default=0,
            ),
            ModuleVariable(
                name="gpu_nodes_count_per_group",
                required=False,
                type_hint="number",
                has_default=True,
                default=0,
            ),
            ModuleVariable(
                name="gpu_nodes_platform",
                required=False,
                type_hint="string",
                has_default=True,
                default="",
            ),
            ModuleVariable(
                name="gpu_nodes_preset",
                required=False,
                type_hint="string",
                has_default=True,
                default="",
            ),
            ModuleVariable(
                name="k8s_version",
                required=False,
                type_hint="string",
                has_default=True,
                default=None,
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
        _ = current, choices, type_hint, required
        prompted_paths.append(path_label)
        if path_label.endswith(".gpu_enabled"):
            return True, False
        if path_label.endswith(".gpu_nodes_platform"):
            return "gpu-h100-sxm", False
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
    assert prompted_paths == [
        "infra.components[0].inputs.cluster_name",
        "infra.components[0].inputs.gpu_enabled",
        "infra.components[0].inputs.gpu_node_groups",
        "infra.components[0].inputs.gpu_nodes_count_per_group",
        "infra.components[0].inputs.gpu_nodes_platform",
        "infra.components[0].inputs.gpu_nodes_preset",
        "infra.components[0].inputs.k8s_version",
    ]


def test_wizard_prompts_infiniband_after_clusterable_gpu_preset(
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
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.gpu_nodes_preset": {
                "options": {
                    "from": "compute_platform_presets",
                    "args": {
                        "platform_path": "inputs.gpu_nodes_platform",
                        "gpu_cluster_required_path": "inputs.infiniband_fabric",
                    },
                    "auto_select_single": True,
                }
            },
            "inputs.infiniband_fabric": {
                "options": {
                    "from": "mk8s_infiniband_fabrics",
                    "args": {
                        "platform_path": "inputs.gpu_nodes_platform",
                        "preset_path": "inputs.gpu_nodes_preset",
                    },
                    "skip_prompt_if_no_choices": True,
                }
            },
        },
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster_name",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
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
            ModuleVariable(
                name="gpu_nodes_preset",
                required=False,
                type_hint="string",
                has_default=True,
                default="",
            ),
            ModuleVariable(
                name="infiniband_fabric",
                required=False,
                type_hint="string",
                has_default=True,
                default="",
            ),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    prompted: list[tuple[str, object]] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required
        prompted.append((path_label, current))
        if path_label.endswith(".gpu_enabled"):
            return True, False
        if path_label.endswith(".gpu_nodes_platform"):
            return "gpu-b200-sxm", False
        if path_label.endswith(".gpu_nodes_preset"):
            return current, False
        if path_label.endswith(".infiniband_fabric"):
            return "us-central1-b", False
        return current, False

    class _Lookup:
        def resolve(self, *, provider, args, payload, field_path):
            _ = args, payload
            if provider == "compute_platform_presets" and field_path.endswith(".gpu_nodes_preset"):
                return [
                    OptionChoice(
                        value="8gpu-160vcpu-1792gb",
                        label="8gpu-160vcpu-1792gb",
                    )
                ]
            if provider == "mk8s_infiniband_fabrics" and field_path.endswith(".infiniband_fabric"):
                return [OptionChoice(value="us-central1-b", label="us-central1-b")]
            return []

        def last_error(self):
            return None

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    _updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_Lookup(),
    )

    assert completed is True
    assert prompted == [
        ("infra.components[0].inputs.cluster_name", None),
        ("infra.components[0].inputs.gpu_enabled", False),
        ("infra.components[0].inputs.gpu_nodes_platform", ""),
        ("infra.components[0].inputs.gpu_nodes_preset", "8gpu-160vcpu-1792gb"),
        ("infra.components[0].inputs.infiniband_fabric", ""),
    ]


def test_wizard_skips_infiniband_for_non_clusterable_gpu_preset(
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
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.gpu_nodes_preset": {
                "options": {
                    "from": "compute_platform_presets",
                    "args": {"platform_path": "inputs.gpu_nodes_platform"},
                }
            },
            "inputs.infiniband_fabric": {
                "options": {
                    "from": "mk8s_infiniband_fabrics",
                    "args": {
                        "platform_path": "inputs.gpu_nodes_platform",
                        "preset_path": "inputs.gpu_nodes_preset",
                    },
                    "skip_prompt_if_no_choices": True,
                }
            },
        },
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster_name",),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
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
            ModuleVariable(
                name="gpu_nodes_preset",
                required=False,
                type_hint="string",
                has_default=True,
                default="",
            ),
            ModuleVariable(
                name="infiniband_fabric",
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
        _ = current, choices, type_hint, required
        prompted_paths.append(path_label)
        if path_label.endswith(".gpu_enabled"):
            return True, False
        if path_label.endswith(".gpu_nodes_platform"):
            return "gpu-b200-sxm", False
        if path_label.endswith(".gpu_nodes_preset"):
            return "1gpu-20vcpu-224gb", False
        return current, False

    class _Lookup:
        def resolve(self, *, provider, args, payload, field_path):
            _ = args, payload
            if provider == "compute_platform_presets" and field_path.endswith(".gpu_nodes_preset"):
                return [
                    OptionChoice(value="1gpu-20vcpu-224gb", label="1gpu-20vcpu-224gb"),
                    OptionChoice(value="8gpu-160vcpu-1792gb", label="8gpu-160vcpu-1792gb"),
                ]
            if provider == "mk8s_infiniband_fabrics" and field_path.endswith(".infiniband_fabric"):
                return []
            return []

        def last_error(self):
            return None

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    _updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_Lookup(),
    )

    assert completed is True
    assert "infra.components[0].inputs.gpu_nodes_preset" in prompted_paths
    assert "infra.components[0].inputs.infiniband_fabric" not in prompted_paths


def test_wizard_clears_stale_infiniband_when_gpu_preset_loses_cluster_support(
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
                        "inputs": {
                            "cluster_name": "cluster1",
                            "gpu_enabled": True,
                            "gpu_nodes_platform": "gpu-b200-sxm",
                            "gpu_nodes_preset": "8gpu-160vcpu-1792gb",
                            "infiniband_fabric": "us-central1-b",
                        },
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
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.gpu_nodes_preset": {
                "options": {
                    "from": "compute_platform_presets",
                    "args": {"platform_path": "inputs.gpu_nodes_platform"},
                }
            },
            "inputs.infiniband_fabric": {
                "options": {
                    "from": "mk8s_infiniband_fabrics",
                    "args": {
                        "platform_path": "inputs.gpu_nodes_platform",
                        "preset_path": "inputs.gpu_nodes_preset",
                    },
                    "skip_prompt_if_no_choices": True,
                }
            },
        },
    )
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(
                name="cluster_name",
                required=True,
                type_hint="string",
                has_default=True,
                default="cluster1",
            ),
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
            ModuleVariable(
                name="gpu_nodes_preset",
                required=False,
                type_hint="string",
                has_default=True,
                default="",
            ),
            ModuleVariable(
                name="infiniband_fabric",
                required=False,
                type_hint="string",
                has_default=True,
                default="",
            ),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required
        if path_label.endswith(".gpu_nodes_preset"):
            return "1gpu-20vcpu-224gb", False
        return current, False

    class _Lookup:
        def resolve(self, *, provider, args, payload, field_path):
            _ = args, payload
            if provider == "compute_platform_presets" and field_path.endswith(".gpu_nodes_preset"):
                return [
                    OptionChoice(value="1gpu-20vcpu-224gb", label="1gpu-20vcpu-224gb"),
                    OptionChoice(value="8gpu-160vcpu-1792gb", label="8gpu-160vcpu-1792gb"),
                ]
            if provider == "mk8s_infiniband_fabrics" and field_path.endswith(".infiniband_fabric"):
                return []
            return []

        def last_error(self):
            return None

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_Lookup(),
    )

    assert completed is True
    payload = yaml.safe_load(updated_yaml)
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["gpu_nodes_preset"] == "1gpu-20vcpu-224gb"
    assert "infiniband_fabric" not in inputs
