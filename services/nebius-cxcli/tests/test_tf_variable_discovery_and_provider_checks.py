from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import nebius_cxcli.cli as cli
from nebius_cxcli.cli import (
    _dynamic_provider_field_checks,
    _materialize_singleton_provider_defaults,
    _materialize_vm_image_defaults,
    _run_component_field_wizard,
)
from nebius_cxcli.component_sources import ComponentDefault, StatusWatcher
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.compute_boot_disks import ComputeBootDiskRecommendationError
from nebius_cxcli.provider_options import OptionChoice, ProviderOptionLookup
from nebius_cxcli.runtime_introspection import (
    ModuleVariable,
    canonical_local_module_source,
    module_required_variables,
    module_variable_names,
    module_variables,
    resolve_module_source_path,
)
from nebius_cxcli.wizard_profiles import BUILTIN_WIZARD_PROFILES


def _static_vpc_choices(provider: str) -> list[OptionChoice]:
    if provider == "project_networks":
        return [OptionChoice(value="vpcnetwork-1", label="default network")]
    if provider == "project_subnets":
        return [OptionChoice(value="vpcsubnet-1", label="default subnet")]
    return []


class _StaticVpcLookup(ProviderOptionLookup):
    def resolve(self, *, provider, args, payload, field_path):
        _ = args, payload, field_path
        return _static_vpc_choices(provider)

    def last_error(self):
        return ""

    def compute_platform_preset_allows_gpu_clustering(self, **_kwargs):
        return False

    def compute_platform_preset_resources(self, *, project_id, platform_name, preset_name):
        _ = project_id, platform_name
        if preset_name == "4vcpu-16gb":
            return (4, 16, 0)
        if preset_name == "16vcpu-64gb":
            return (16, 64, 0)
        if preset_name == "1gpu-16vcpu-200gb":
            return (16, 200, 1)
        if preset_name == "8gpu-128vcpu-1600gb":
            return (128, 1600, 8)
        return None

    def compute_platform_preset_fabrics(self, **_kwargs):
        return ()


@pytest.fixture(autouse=True)
def _disable_live_gpu_capacity_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_maybe_print_live_gpu_capacity_summary", lambda **_kwargs: None)


def test_prompt_path_sort_key_orders_mk8s_cpu_defaults_before_gpu_defaults() -> None:
    paths = [
        ("infra", "components", 0, "inputs", "node_group_defaults", "gpu", "preset"),
        (
            "infra",
            "components",
            0,
            "inputs",
            "node_group_defaults",
            "gpu",
            "reservation",
            "policy",
        ),
        ("infra", "components", 0, "inputs", "node_group_defaults", "cpu", "preset"),
        ("infra", "components", 0, "inputs", "node_group_defaults", "gpu", "platform"),
        ("infra", "components", 0, "inputs", "node_group_defaults", "cpu", "platform"),
    ]

    ordered = sorted(
        paths, key=lambda path: cli._prompt_path_sort_key(path, required_leaf_names=set())
    )

    assert ordered == [
        ("infra", "components", 0, "inputs", "node_group_defaults", "cpu", "platform"),
        ("infra", "components", 0, "inputs", "node_group_defaults", "cpu", "preset"),
        ("infra", "components", 0, "inputs", "node_group_defaults", "gpu", "platform"),
        (
            "infra",
            "components",
            0,
            "inputs",
            "node_group_defaults",
            "gpu",
            "reservation",
            "policy",
        ),
        ("infra", "components", 0, "inputs", "node_group_defaults", "gpu", "preset"),
    ]


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


def test_local_package_module_source_resolves_subdir_without_losing_package_root(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "platform-infra"
    module_dir = package_root / "modules" / "wireguard-gw"
    module_dir.mkdir(parents=True)
    (module_dir / "variables.tf").write_text(
        'variable "name" { type = string }\n',
        encoding="utf-8",
    )

    source = f"{package_root}//modules/wireguard-gw"

    assert resolve_module_source_path(source) == module_dir
    assert canonical_local_module_source(source) == source
    assert module_variable_names(source) == ("name",)


def test_module_variable_discovery_preserves_multiline_object_type(tmp_path: Path) -> None:
    module_dir = tmp_path / "demo-module"
    module_dir.mkdir(parents=True)
    (module_dir / "variables.tf").write_text(
        """
variable "secrets" {
  type = list(object({
    name = string
    payload = map(object({
      type = optional(string, "text")
    }))
  }))
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    specs = {item.name: item for item in module_variables(str(module_dir))}
    file_specs = {item.name: item for item in module_variables(module_dir.as_uri())}

    assert specs["secrets"].type_hint == file_specs["secrets"].type_hint
    assert specs["secrets"].type_hint is not None
    assert specs["secrets"].type_hint.startswith("list(object({")
    assert 'optional(string, "text")' in specs["secrets"].type_hint


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
    assert sfs_specs["name"].required is False
    assert sfs_specs["name"].nullable is True
    assert sfs_specs["size_gib"].required is False
    assert sfs_specs["size_gib"].nullable is True
    assert sfs_specs["filesystems"].required is False
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
    node_groups_type_hint = specs["node_groups"].type_hint
    assert node_groups_type_hint is not None

    assert specs["cluster"].required is True
    assert specs["node_groups"].required is True
    assert "gpu_stack_source" in node_groups_type_hint
    assert "gpu_stack_preset" in node_groups_type_hint
    assert "strategy    = optional(any)" not in node_groups_type_hint
    assert "strategy = optional(object({" in node_groups_type_hint
    assert "max_unavailable" in node_groups_type_hint
    assert "gpu_stack_source" not in specs
    assert "cpu_nodes_os" not in specs
    assert "gpu_nodes_os" not in specs
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


def test_vpc_module_subnet_contract_includes_attached_private_pool_cidrs() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    vpc_dir = repo_root / "platform-infra" / "modules" / "vpc"
    locals_tf = (vpc_dir / "locals.tf").read_text(encoding="utf-8")
    main_tf = (vpc_dir / "main.tf").read_text(encoding="utf-8")

    assert "input_network_private_pool_cidrs" in locals_tf
    assert "existing_network_private_pool_cidrs" in locals_tf
    assert "data.nebius_vpc_v1_pool.private_pool" in locals_tf
    assert 'data "nebius_vpc_v1_pool" "existing_private_pool"' in main_tf
    assert "network_private_cidrs = distinct(concat(" in locals_tf
    assert "local.existing_network_private_pool_cidrs," in locals_tf
    assert "local.input_network_private_pool_cidrs," in locals_tf
    assert "for network_range in local.network_private_ranges" in main_tf
    assert (
        "!local.create_network"
        not in main_tf.split(
            'resource "terraform_data" "subnet_contract"',
            maxsplit=1,
        )[1]
    )


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
            "apps": {
                "charts": [
                    {
                        "id": "soperator",
                        "instance_id": "mk8s",
                        "enabled": True,
                        "install_mode": "production-cluster",
                        "values": {},
                    }
                ]
            },
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, required, unset_on_skip
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


def test_wizard_uses_guided_mk8s_cluster_and_node_group_fields(
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

    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="Managed Kubernetes",
        source="../../platform-infra/modules/mk8s",
        wizard_fields=BUILTIN_WIZARD_PROFILES["mk8s"],
        status=StatusWatcher(
            kind="nebius.mk8s.cluster",
            parent_input="cluster.parent_id",
            name_input="cluster.cluster_name",
        ),
    )

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster", "node_groups"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(
                name="cluster",
                required=True,
                type_hint=(
                    "object({ parent_id = string, cluster_name = string, "
                    "network_id = string, subnet_id = string, k8s_version = string, "
                    "public_endpoint = bool })"
                ),
            ),
            ModuleVariable(
                name="node_groups",
                required=True,
                type_hint="map(object({ platform = string, preset = string }))",
            ),
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )

    prompted_paths: list[str] = []
    new_group_names = ["system", "burst"]
    add_another_answers = [True, False]

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
        prompted_paths.append(path_label)
        if path_label.endswith(".inputs.cluster.cluster_name"):
            return "demo-cluster", False
        if path_label.endswith(".inputs.cluster.network_id"):
            return "vpcnetwork-1", False
        if path_label.endswith(".inputs.cluster.subnet_id"):
            return "vpcsubnet-1", False
        if path_label.endswith(".inputs.cluster.k8s_version"):
            return "1.33", False
        if path_label.endswith(".inputs.cluster.public_endpoint"):
            return current if current is not None else True, False
        if path_label.endswith(".inputs.node_groups.<new>.name"):
            if not new_group_names:
                pytest.fail("unexpected extra MK8s node group prompt")
            return new_group_names.pop(0), False
        if path_label.endswith(".inputs.node_groups.system.autoscaling.enabled"):
            return False, False
        if path_label.endswith(".inputs.node_groups.burst.autoscaling.enabled"):
            return True, False
        if path_label.endswith(".inputs.node_groups.burst.autoscaling.min_node_count"):
            return 1, False
        if path_label.endswith(".inputs.node_groups.burst.autoscaling.max_node_count"):
            return 4, False
        if path_label.endswith(".inputs.node_groups.system.node_count"):
            return current if current is not None else 2, False
        if path_label.endswith(".inputs.node_groups.burst.node_count"):
            pytest.fail("autoscaled MK8s node group should not prompt for node_count")
        if path_label.endswith(".inputs.node_groups.system.resource"):
            return "cpu", False
        if path_label.endswith(".inputs.node_groups.burst.resource"):
            return "cpu", False
        if path_label.endswith(".inputs.node_groups.system.preemptible"):
            return False, False
        if path_label.endswith(".inputs.node_groups.burst.preemptible"):
            return False, False
        if path_label.endswith(".inputs.node_groups.system.platform"):
            return "cpu-d3", False
        if path_label.endswith(".inputs.node_groups.burst.platform"):
            return "cpu-d3", False
        if path_label.endswith(".inputs.node_groups.system.preset"):
            return "4vcpu-16gb", False
        if path_label.endswith(".inputs.node_groups.burst.preset"):
            return "16vcpu-64gb", False
        if path_label.endswith(".inputs.node_groups.system.boot_disk.type"):
            return "NETWORK_SSD", False
        if path_label.endswith(".inputs.node_groups.burst.boot_disk.type"):
            return "NETWORK_SSD", False
        if path_label.endswith(".inputs.node_groups.system.boot_disk.size_gibibytes"):
            assert current == 64
            return current, False
        if path_label.endswith(".inputs.node_groups.burst.boot_disk.size_gibibytes"):
            return current, False
        if path_label.endswith(".inputs.node_groups.system.ssh.enabled"):
            assert current is True
            return True, False
        if path_label.endswith(".inputs.node_groups.burst.ssh.enabled"):
            assert current is True
            return False, False
        if path_label.endswith(".inputs.node_groups.system.ssh.username"):
            return "ubuntu", False
        if path_label.endswith(".inputs.node_groups.burst.ssh.username"):
            pytest.fail("disabled SSH should not prompt for username")
        if path_label.endswith(".inputs.node_groups.system.service_account.mode"):
            return "none", False
        if path_label.endswith(".inputs.node_groups.burst.service_account.mode"):
            return "none", False
        if path_label.endswith(".inputs.node_groups.add_another"):
            if not add_another_answers:
                pytest.fail("unexpected extra MK8s node group add-another prompt")
            return add_another_answers.pop(0), False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)
    monkeypatch.setattr(
        "nebius_cxcli.cli._prompt_ssh_public_key_override",
        lambda *_args, **_kwargs: ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKey test", False),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._provider_allowed_values_for_field",
        lambda **_kwargs: (set(), ()),
    )

    def _fake_provider_choices(**kwargs):  # type: ignore[no-untyped-def]
        full_path_label = kwargs["full_path_label"]
        if full_path_label.endswith(".inputs.cluster.network_id"):
            return [OptionChoice(value="vpcnetwork-1", label="default network")]
        if full_path_label.endswith(".inputs.cluster.subnet_id"):
            return [OptionChoice(value="vpcsubnet-1", label="default subnet")]
        if full_path_label.endswith(".inputs.cluster.k8s_version"):
            return [OptionChoice(value="1.33", label="1.33")]
        return []

    monkeypatch.setattr(
        "nebius_cxcli.cli._resolve_dynamic_field_choices",
        _fake_provider_choices,
    )

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_StaticVpcLookup(),
    )

    assert completed is True
    assert "infra.components[0].inputs.cluster" not in prompted_paths
    assert "infra.components[0].inputs.node_groups" not in prompted_paths
    assert "infra.components[0].inputs.cluster.cluster_name" in prompted_paths
    assert "infra.components[0].inputs.cluster.network_id" in prompted_paths
    assert "infra.components[0].inputs.cluster.subnet_id" in prompted_paths
    assert "infra.components[0].inputs.cluster.k8s_version" in prompted_paths
    assert "infra.components[0].inputs.node_groups.<new>.name" in prompted_paths
    assert "infra.components[0].inputs.node_groups.system.resource" in prompted_paths
    assert "infra.components[0].inputs.node_groups.system.autoscaling.enabled" in prompted_paths
    assert "infra.components[0].inputs.node_groups.system.platform" in prompted_paths
    assert "infra.components[0].inputs.node_groups.system.preset" in prompted_paths
    assert "infra.components[0].inputs.node_groups.burst.resource" in prompted_paths
    assert "infra.components[0].inputs.node_groups.burst.autoscaling.enabled" in prompted_paths
    assert (
        "infra.components[0].inputs.node_groups.burst.autoscaling.min_node_count" in prompted_paths
    )
    assert (
        "infra.components[0].inputs.node_groups.burst.autoscaling.max_node_count" in prompted_paths
    )
    assert "infra.components[0].inputs.node_groups.burst.node_count" not in prompted_paths
    assert "infra.components[0].inputs.node_groups.add_another" in prompted_paths
    assert not new_group_names
    assert not add_another_answers

    payload = yaml.safe_load(updated_yaml)
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["cluster"]["parent_id"] == "project-1"
    assert inputs["cluster"]["cluster_name"] == "demo-cluster"
    assert inputs["cluster"]["network_id"] == "vpcnetwork-1"
    assert inputs["cluster"]["subnet_id"] == "vpcsubnet-1"
    assert inputs["cluster"]["k8s_version"] == "1.33"
    assert inputs["cluster"]["public_endpoint"] is True
    assert "node_group_defaults" not in inputs
    assert inputs["node_groups"]["system"]["node_count"] == 2
    assert inputs["node_groups"]["system"]["gpu"] is False
    assert inputs["node_groups"]["system"]["platform"] == "cpu-d3"
    assert inputs["node_groups"]["system"]["preset"] == "4vcpu-16gb"
    assert inputs["node_groups"]["burst"]["autoscaling"] == {
        "min_node_count": 1,
        "max_node_count": 4,
    }
    assert "node_count" not in inputs["node_groups"]["burst"]
    assert inputs["node_groups"]["burst"]["gpu"] is False
    assert inputs["node_groups"]["burst"]["platform"] == "cpu-d3"
    assert inputs["node_groups"]["burst"]["preset"] == "16vcpu-64gb"
    assert inputs["node_groups"]["system"]["boot_disk"] == {
        "type": "NETWORK_SSD",
        "size_gibibytes": 64,
    }
    assert inputs["node_groups"]["system"]["ssh"] == {
        "username": "ubuntu",
        "public_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKey test"],
    }


def test_wizard_auto_enables_gpu_apps_after_plain_mk8s_gpu_node_group_loop(
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
        wizard_fields=BUILTIN_WIZARD_PROFILES["mk8s"],
        status=StatusWatcher(
            kind="nebius.mk8s.cluster",
            parent_input="cluster.parent_id",
            name_input="cluster.cluster_name",
        ),
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
        lambda _source: ("cluster", "node_groups"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(
                name="cluster",
                required=True,
                type_hint=(
                    "object({ parent_id = string, cluster_name = string, "
                    "network_id = string, subnet_id = string, k8s_version = string, "
                    "public_endpoint = bool })"
                ),
            ),
            ModuleVariable(
                name="node_groups",
                required=True,
                type_hint="map(object({ platform = string, preset = string }))",
            ),
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr("nebius_cxcli.cli._app_chart_default_values", lambda **_kwargs: {})

    phase_prompts: list[tuple[str, bool]] = []

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = allow_back
        phase_prompts.append((label, default))
        return default

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)

    def _fake_gpu_selection(payload, *, selected_app_ids=None, app_entries=None):
        _ = selected_app_ids, app_entries
        inputs = payload["infra"]["components"][0]["inputs"]
        node_groups = inputs.get("node_groups", {})
        if any(isinstance(group, dict) and group.get("gpu") for group in node_groups.values()):
            return SimpleNamespace(
                selected_app_ids=("nvidia-gpu-operator",),
                auto_enabled_app_ids=("nvidia-gpu-operator",),
                issues=(),
            )
        return SimpleNamespace(
            selected_app_ids=tuple(sorted(selected_app_ids or ())),
            auto_enabled_app_ids=(),
            issues=(),
        )

    monkeypatch.setattr("nebius_cxcli.cli.resolve_mk8s_gpu_app_selection", _fake_gpu_selection)

    prompted_paths: list[str] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
        prompted_paths.append(path_label)
        if path_label.endswith(".inputs.cluster.cluster_name"):
            return "demo-cluster", False
        if path_label.endswith(".inputs.cluster.network_id"):
            return "vpcnetwork-1", False
        if path_label.endswith(".inputs.cluster.subnet_id"):
            return "vpcsubnet-1", False
        if path_label.endswith(".inputs.cluster.k8s_version"):
            return "1.33", False
        if path_label.endswith(".inputs.cluster.public_endpoint"):
            return current if current is not None else True, False
        if path_label.endswith(".inputs.node_groups.<new>.name"):
            return "gpu", False
        if path_label.endswith(".inputs.node_groups.gpu.autoscaling.enabled"):
            return False, False
        if path_label.endswith(".inputs.node_groups.gpu.node_count"):
            return 1, False
        if path_label.endswith(".inputs.node_groups.gpu.resource"):
            return "gpu", False
        if path_label.endswith(".inputs.node_groups.gpu.preemptible"):
            return False, False
        if path_label.endswith(".inputs.node_groups.gpu.platform"):
            return "gpu-h100-sxm", False
        if path_label.endswith(".inputs.node_groups.gpu.preset"):
            return "1gpu-16vcpu-200gb", False
        if path_label.endswith(".inputs.node_groups.gpu.gpu_stack_source"):
            return "nebius_image", False
        if path_label.endswith(".inputs.node_groups.gpu.gpu_stack_preset"):
            return "cuda12", False
        if path_label.endswith(".inputs.node_groups.gpu.reservation.policy"):
            assert current == "AUTO"
            return "AUTO", False
        if path_label.endswith(".inputs.node_groups.gpu.boot_disk.type"):
            return "NETWORK_SSD", False
        if path_label.endswith(".inputs.node_groups.gpu.boot_disk.size_gibibytes"):
            assert current == 256
            return current, False
        if path_label.endswith(".inputs.node_groups.gpu.ssh.enabled"):
            assert current is True
            return False, False
        if path_label.endswith(".inputs.node_groups.gpu.service_account.mode"):
            return "none", False
        if path_label.endswith(".inputs.node_groups.add_another"):
            return False, False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    class _Lookup(ProviderOptionLookup):
        def resolve(self, *, provider, args, payload, field_path):
            _ = args, payload, field_path
            vpc_choices = _static_vpc_choices(provider)
            if vpc_choices:
                return vpc_choices
            if provider == "mk8s_gpu_stack_presets":
                return [OptionChoice(value="cuda13.0", label="cuda13.0  (ubuntu24.04)")]
            if provider == "mk8s_node_group_os_values":
                return [OptionChoice(value="ubuntu24.04", label="ubuntu24.04")]
            if provider == "compute_boot_disk_types":
                return [OptionChoice(value="NETWORK_SSD", label="NETWORK_SSD")]
            return []

        def last_error(self):
            return ""

        def compute_platform_preset_allows_gpu_clustering(self, **_kwargs):
            return False

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(infra_entry,),
        app_entries=(gpu_entry,),
        provider_lookup=_Lookup(),
    )

    assert completed is True
    assert phase_prompts == [
        ("Configure 'mk8s' component fields now?", True),
        ("Configure 'nvidia-gpu-operator on mk8s' component fields now?", False),
    ]
    assert "infra.components[0].inputs.node_groups.gpu.resource" in prompted_paths
    assert "infra.components[0].inputs.node_groups.gpu.gpu_stack_source" in prompted_paths
    assert "infra.components[0].inputs.node_groups.gpu.os" not in prompted_paths
    payload = yaml.safe_load(updated_yaml)
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["node_groups"]["gpu"]["gpu"] is True
    assert inputs["node_groups"]["gpu"]["platform"] == "gpu-h100-sxm"
    assert inputs["node_groups"]["gpu"]["gpu_stack_source"] == "nebius_image"
    assert inputs["node_groups"]["gpu"]["os"] == "ubuntu24.04"
    assert inputs["node_groups"]["gpu"]["boot_disk"] == {
        "type": "NETWORK_SSD",
        "size_gibibytes": 256,
    }
    assert [item["id"] for item in payload["apps"]["charts"]] == ["nvidia-gpu-operator"]


def test_wizard_back_inside_plain_mk8s_node_group_stays_in_group_loop(
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
                    "region_id": "eu-north1",
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
                        "inputs": {
                            "cluster": {
                                "parent_id": "project-1",
                                "cluster_name": "demo-cluster",
                                "network_id": "vpcnetwork-1",
                                "subnet_id": "vpcsubnet-1",
                                "k8s_version": "1.33",
                                "public_endpoint": True,
                            },
                            "node_groups": {
                                "cpu": {
                                    "node_count": 2,
                                    "gpu": False,
                                    "platform": "cpu-d3",
                                    "preset": "4vcpu-16gb",
                                }
                            },
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
        wizard_fields=BUILTIN_WIZARD_PROFILES["mk8s"],
        status=StatusWatcher(
            kind="nebius.mk8s.cluster",
            parent_input="cluster.parent_id",
            name_input="cluster.cluster_name",
        ),
    )

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster", "node_groups"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="cluster", required=True, type_hint="object({})"),
            ModuleVariable(name="node_groups", required=True, type_hint="map(object({}))"),
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    prompted_paths: list[str] = []
    add_another_count = 0
    stack_preset_back_once = True

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        nonlocal add_another_count, stack_preset_back_once
        _ = choices, type_hint, required, unset_on_skip
        prompted_paths.append(path_label)
        if ".inputs.cluster." in path_label:
            return current, False
        if path_label.endswith(".inputs.node_groups.add_another"):
            add_another_count += 1
            return add_another_count == 1, False
        if path_label.endswith(".inputs.node_groups.<new>.name"):
            return "gpu-nodeg2", False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.autoscaling.enabled"):
            return False, False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.node_count"):
            return 1, False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.resource"):
            return "gpu", False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.preemptible"):
            return False, False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.platform"):
            return "gpu-h100-sxm", False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.preset"):
            return "1gpu-16vcpu-200gb", False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.gpu_stack_source"):
            return "nebius_image", False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.gpu_stack_preset"):
            if stack_preset_back_once:
                stack_preset_back_once = False
                return cli._WIZARD_BACKTRACK, False
            return "cuda13.0", False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.reservation.policy"):
            return "FORBID", False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.os"):
            return current, False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.boot_disk.type"):
            return "NETWORK_SSD", False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.boot_disk.size_gibibytes"):
            return current, False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.ssh.enabled"):
            return False, False
        if path_label.endswith(".inputs.node_groups.gpu-nodeg2.service_account.mode"):
            return "none", False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_StaticVpcLookup(),
    )

    assert completed is True
    stack_prompt_index = prompted_paths.index(
        "infra.components[0].inputs.node_groups.gpu-nodeg2.gpu_stack_preset"
    )
    assert prompted_paths[stack_prompt_index + 1].endswith(".inputs.node_groups.<new>.name")
    payload = yaml.safe_load(updated_yaml)
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["node_groups"]["gpu-nodeg2"]["gpu_stack_preset"] == "cuda13.0"
    assert "infra.components[0].inputs.node_groups.gpu-nodeg2.gpu_cluster.enabled" not in (
        prompted_paths
    )


def test_wizard_back_after_plain_mk8s_gpu_cluster_removes_orphan_fabric(
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
                    "region_id": "eu-north1",
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
                        "inputs": {
                            "cluster": {
                                "parent_id": "project-1",
                                "cluster_name": "demo-cluster",
                                "network_id": "vpcnetwork-1",
                                "subnet_id": "vpcsubnet-1",
                                "k8s_version": "1.33",
                                "public_endpoint": True,
                            },
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
        wizard_fields=BUILTIN_WIZARD_PROFILES["mk8s"],
        status=StatusWatcher(
            kind="nebius.mk8s.cluster",
            parent_input="cluster.parent_id",
            name_input="cluster.cluster_name",
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster", "node_groups"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="cluster", required=True, type_hint="object({})"),
            ModuleVariable(name="node_groups", required=True, type_hint="map(object({}))"),
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    resource_prompt_count = 0
    reservation_ids_back_once = True
    prompted_paths: list[str] = []
    resolved_fabric_fields: list[str] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        nonlocal resource_prompt_count, reservation_ids_back_once
        _ = choices, type_hint, required, unset_on_skip
        prompted_paths.append(path_label)
        if ".inputs.cluster." in path_label:
            return current, False
        if path_label.endswith(".inputs.node_groups.add_another"):
            return False, False
        if path_label.endswith(".inputs.node_groups.<new>.name"):
            return "gpu-nodeg2", False
        if path_label.endswith(".autoscaling.enabled"):
            return False, False
        if path_label.endswith(".node_count"):
            return 1, False
        if path_label.endswith(".resource"):
            resource_prompt_count += 1
            return ("gpu" if resource_prompt_count == 1 else "cpu"), False
        if path_label.endswith(".preemptible"):
            return False, False
        if path_label.endswith(".platform"):
            return ("gpu-h100-sxm" if resource_prompt_count == 1 else "cpu-d3"), False
        if path_label.endswith(".preset"):
            return ("8gpu-128vcpu-1600gb" if resource_prompt_count == 1 else "4vcpu-16gb"), False
        if path_label.endswith(".gpu_stack_source"):
            return "nebius_image", False
        if path_label.endswith(".gpu_stack_preset"):
            return "cuda13.0", False
        if path_label.endswith(".reservation.policy"):
            assert current == "AUTO"
            return "AUTO", False
        if path_label.endswith(".reservation.reservation_ids"):
            if reservation_ids_back_once:
                reservation_ids_back_once = False
                return cli._WIZARD_BACKTRACK, False
            return current, False
        if path_label.endswith(".boot_disk.type"):
            return "NETWORK_SSD", False
        if path_label.endswith(".boot_disk.size_gibibytes"):
            return current, False
        if path_label.endswith(".ssh.enabled"):
            return False, False
        if path_label.endswith(".service_account.mode"):
            return "none", False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    class _Lookup(ProviderOptionLookup):
        def resolve(self, *, provider, args, payload, field_path):
            _ = args, payload, field_path
            vpc_choices = _static_vpc_choices(provider)
            if vpc_choices:
                return vpc_choices
            if provider == "mk8s_gpu_stack_presets":
                return [OptionChoice(value="cuda13.0", label="cuda13.0  (ubuntu24.04)")]
            if provider == "mk8s_node_group_os_values":
                return [OptionChoice(value="ubuntu24.04", label="ubuntu24.04")]
            if provider == "mk8s_infiniband_fabrics":
                resolved_fabric_fields.append(field_path)
                return [
                    OptionChoice(
                        value="fabric-2",
                        label=(
                            "fabric-2  (gpu-h100-sxm, eu-north1), regular-vm 0 VMs, "
                            "reserved 1 VM (1 x 8-GPU = 8 GPUs), recommended for reservations"
                        ),
                        metadata={"reserved_vms": 1},
                    )
                ]
            if provider == "compute_boot_disk_types":
                return [OptionChoice(value="NETWORK_SSD", label="NETWORK_SSD")]
            return []

        def last_error(self):
            return ""

        def compute_platform_preset_allows_gpu_clustering(self, **_kwargs):
            return True

        def compute_platform_preset_resources(self, *, project_id, platform_name, preset_name):
            _ = project_id
            if platform_name.startswith("gpu-"):
                return (128, 1600, 8)
            if preset_name == "4vcpu-16gb":
                return (4, 16, 0)
            return None

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=_Lookup(),
    )

    assert completed is True
    assert (
        "infra.components[0].inputs.gpu_clusters.gpu-nodeg2.infiniband_fabric"
        in resolved_fabric_fields
    )
    assert "infra.components[0].inputs.gpu_clusters.gpu-nodeg2.infiniband_fabric" not in (
        prompted_paths
    )
    assert "infra.components[0].inputs.node_groups.gpu-nodeg2.gpu_cluster.enabled" not in (
        prompted_paths
    )
    payload = yaml.safe_load(updated_yaml)
    inputs = payload["infra"]["components"][0]["inputs"]
    assert "gpu_clusters" not in inputs
    assert inputs["node_groups"]["gpu-nodeg2"]["gpu"] is False
    assert inputs["node_groups"]["gpu-nodeg2"]["platform"] == "cpu-d3"


def test_wizard_plain_mk8s_boot_disk_policy_errors_are_not_hidden(
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
                    "region_id": "eu-north1",
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
                        "inputs": {
                            "cluster": {
                                "parent_id": "project-1",
                                "cluster_name": "demo-cluster",
                                "network_id": "vpcnetwork-1",
                                "subnet_id": "vpcsubnet-1",
                                "k8s_version": "1.33",
                                "public_endpoint": True,
                            },
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
        wizard_fields=BUILTIN_WIZARD_PROFILES["mk8s"],
        status=StatusWatcher(
            kind="nebius.mk8s.cluster",
            parent_input="cluster.parent_id",
            name_input="cluster.cluster_name",
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("cluster", "node_groups"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="cluster", required=True, type_hint="object({})"),
            ModuleVariable(name="node_groups", required=True, type_hint="map(object({}))"),
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
        if ".inputs.cluster." in path_label:
            return current, False
        if path_label.endswith(".inputs.node_groups.<new>.name"):
            return "system", False
        if path_label.endswith(".autoscaling.enabled"):
            return False, False
        if path_label.endswith(".node_count"):
            return 1, False
        if path_label.endswith(".resource"):
            return "cpu", False
        if path_label.endswith(".preemptible"):
            return False, False
        if path_label.endswith(".platform"):
            return "cpu-d3", False
        if path_label.endswith(".preset"):
            return "custom-unmatched", False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    class _Lookup(ProviderOptionLookup):
        def resolve(self, *, provider, args, payload, field_path):
            _ = args, payload, field_path
            vpc_choices = _static_vpc_choices(provider)
            if vpc_choices:
                return vpc_choices
            return []

        def last_error(self):
            return ""

        def compute_platform_preset_resources(self, *, project_id, platform_name, preset_name):
            _ = project_id, platform_name, preset_name
            return (None, None, None)

    with pytest.raises(ComputeBootDiskRecommendationError, match="No compute.boot_disk_defaults"):
        _run_component_field_wizard(
            config_yaml=config_yaml,
            selected_infra={"mk8s"},
            selected_apps=set(),
            infra_entries=(entry,),
            app_entries=(),
            provider_lookup=_Lookup(),
        )


def test_nested_status_name_input_can_seed_complex_module_object(
    monkeypatch,
) -> None:
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="Managed Kubernetes",
        source="../../platform-infra/modules/mk8s",
        wizard_fields=BUILTIN_WIZARD_PROFILES["mk8s"],
        status=StatusWatcher(
            kind="nebius.mk8s.cluster",
            parent_input="cluster.parent_id",
            name_input="cluster.cluster_name",
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (ModuleVariable(name="cluster", required=True, type_hint="object({})"),),
    )

    assert cli._entry_scalar_resource_name_input(entry) == "cluster.cluster_name"

    row = {"id": "mk8s", "instance_id": "demo-cluster", "enabled": True, "inputs": {}}
    cli._seed_infra_resource_name_from_instance_id(row, entry)

    assert row["inputs"] == {"cluster": {"cluster_name": "demo-cluster"}}


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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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
    monkeypatch.setattr("nebius_cxcli.cli._app_chart_default_values", lambda **_kwargs: {})

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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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


def test_wizard_auto_enabled_observability_apps_stay_scoped_to_added_mk8s_target(
    monkeypatch,
) -> None:
    app_ids = ("nebius-observability-agent", "grafana", "gateway-helm")
    config_yaml = yaml.safe_dump(
        {
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
                        "id": "mk8s",
                        "instance_id": "cluster1",
                        "enabled": True,
                        "inputs": {},
                    },
                    {
                        "id": "mk8s",
                        "instance_id": "cluster2",
                        "enabled": True,
                        "inputs": {},
                    },
                ]
            },
            "apps": {
                "charts": [
                    {
                        "id": app_id,
                        "instance_id": "cluster1",
                        "enabled": True,
                        "namespace": "observability",
                        "release-name": app_id,
                        "values": {},
                    }
                    for app_id in app_ids
                ]
            },
            "deploy": {
                "targets": [
                    {
                        "instance_id": "cluster1",
                        "observability": {
                            "enabled": True,
                            "kubernetes": {"logs": {"enabled": True}},
                        },
                    },
                    {
                        "instance_id": "cluster2",
                        "observability": {
                            "enabled": False,
                            "kubernetes": {"logs": {"enabled": True}},
                        },
                    },
                ]
            },
        },
        sort_keys=False,
    )
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.components.mk8s",
        description="MK8s",
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "deploy.targets[].observability.enabled": {"type_hint": "bool"},
            "deploy.targets[].observability.kubernetes.logs.enabled": {"type_hint": "bool"},
        },
    )
    app_entries = tuple(
        ComponentEntry(
            id=app_id,
            scope="apps",
            config_path=f"apps.charts.{app_id}",
            description=app_id,
            chart_name=app_id,
            default_namespace="observability",
            default_release_name=app_id,
        )
        for app_id in app_ids
    )

    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
        prompted_paths.append(path_label)
        if path_label == "deploy.targets[1].observability.enabled":
            return True, False
        if path_label == "deploy.targets[1].observability.kubernetes.logs.enabled":
            return True, False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"cluster2"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=app_entries,
        provider_lookup=None,
    )

    assert completed is True
    assert "deploy.targets[1].observability.kubernetes.logs.enabled" in prompted_paths
    rendered = "\n".join(rendered_messages)
    assert "mk8s@cluster2" in rendered
    assert "nebius-observability-agent on cluster2" in rendered
    assert "nebius-observability-agent on cluster1" not in rendered

    payload = yaml.safe_load(updated_yaml)
    chart_targets = sorted(
        (row["id"], row["instance_id"])
        for row in payload["apps"]["charts"]
        if isinstance(row, dict) and row.get("id") in app_ids and row.get("enabled") is True
    )
    assert chart_targets == sorted(
        (app_id, target_ref) for app_id in app_ids for target_ref in ("cluster1", "cluster2")
    )
    assert not any(
        row["id"] == row["instance_id"]
        for row in payload["apps"]["charts"]
        if isinstance(row, dict) and row.get("id") in app_ids
    )


def test_wizard_removes_backtracked_observability_apps_for_added_target_only(
    monkeypatch,
) -> None:
    app_ids = ("nebius-observability-agent", "grafana", "gateway-helm")
    config_yaml = yaml.safe_dump(
        {
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
                        "id": "mk8s",
                        "instance_id": "cluster1",
                        "enabled": True,
                        "inputs": {},
                    },
                    {
                        "id": "mk8s",
                        "instance_id": "cluster2",
                        "enabled": True,
                        "inputs": {},
                    },
                ]
            },
            "apps": {
                "charts": [
                    {
                        "id": app_id,
                        "instance_id": "cluster1",
                        "enabled": True,
                        "namespace": "observability",
                        "release-name": app_id,
                        "values": {},
                    }
                    for app_id in app_ids
                ]
            },
            "deploy": {
                "targets": [
                    {
                        "instance_id": "cluster1",
                        "observability": {
                            "enabled": True,
                            "kubernetes": {"logs": {"enabled": True}},
                        },
                    },
                    {
                        "instance_id": "cluster2",
                        "observability": {
                            "enabled": False,
                            "kubernetes": {"logs": {"enabled": True}},
                        },
                    },
                ]
            },
        },
        sort_keys=False,
    )
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.components.mk8s",
        description="MK8s",
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "deploy.targets[].observability.enabled": {"type_hint": "bool"},
            "deploy.targets[].observability.kubernetes.logs.enabled": {"type_hint": "bool"},
        },
    )
    app_entries = tuple(
        ComponentEntry(
            id=app_id,
            scope="apps",
            config_path=f"apps.charts.{app_id}",
            description=app_id,
            chart_name=app_id,
            default_namespace="observability",
            default_release_name=app_id,
        )
        for app_id in app_ids
    )

    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr("nebius_cxcli.cli.console.print", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "nebius_cxcli.cli._wizard_continue_phase",
        lambda label, *_args, **_kwargs: label == "Configure 'mk8s@cluster2' component fields now?",
    )

    answers = {
        "deploy.targets[1].observability.enabled": [True, False],
        "deploy.targets[1].observability.kubernetes.logs.enabled": [cli._WIZARD_BACKTRACK],
    }

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
        pending = answers.get(path_label)
        if pending:
            return pending.pop(0), False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"cluster2"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=app_entries,
        provider_lookup=None,
    )

    assert completed is True
    payload = yaml.safe_load(updated_yaml)
    assert payload["deploy"]["targets"][1]["observability"]["enabled"] is False
    chart_targets = sorted(
        (row["id"], row["instance_id"])
        for row in payload["apps"]["charts"]
        if isinstance(row, dict) and row.get("id") in app_ids and row.get("enabled") is True
    )
    assert chart_targets == sorted((app_id, "cluster1") for app_id in app_ids)


def test_wizard_gpu_auto_apps_preserve_target_scoped_app_selection(
    monkeypatch,
) -> None:
    existing_app_ids = ("nebius-observability-agent", "grafana", "gateway-helm")
    gpu_app_ids = ("nvidia-gpu-operator", "nvidia-network-operator")
    config_yaml = yaml.safe_dump(
        {
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
                        "id": "mk8s",
                        "instance_id": "cluster1",
                        "enabled": True,
                        "inputs": {},
                    },
                    {
                        "id": "mk8s",
                        "instance_id": "cluster2",
                        "enabled": True,
                        "inputs": {},
                    },
                ]
            },
            "apps": {
                "charts": [
                    {
                        "id": app_id,
                        "instance_id": target_ref,
                        "enabled": True,
                        "namespace": "observability",
                        "release-name": app_id,
                        "values": {},
                    }
                    for target_ref in ("cluster1", "cluster2")
                    for app_id in existing_app_ids
                ]
            },
            "deploy": {
                "targets": [
                    {
                        "instance_id": "cluster1",
                        "observability": {"enabled": True},
                    },
                    {
                        "instance_id": "cluster2",
                        "observability": {"enabled": True},
                    },
                ]
            },
        },
        sort_keys=False,
    )
    infra_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.components.mk8s",
        description="MK8s",
        source="../../platform-infra/modules/mk8s",
    )
    app_entries = tuple(
        ComponentEntry(
            id=app_id,
            scope="apps",
            config_path=f"apps.charts.{app_id}",
            description=app_id,
            chart_name=app_id,
            default_namespace="platform",
            default_release_name=app_id,
        )
        for app_id in (*existing_app_ids, *gpu_app_ids)
    )

    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )

    phase_prompts: list[str] = []

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = default, allow_back
        phase_prompts.append(label)
        return label == "Configure 'mk8s@cluster2' component fields now?"

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)

    def _fake_gpu_selection(payload, *, selected_app_ids=None, app_entries=None):
        _ = payload, app_entries
        selected = set(selected_app_ids or ())
        return SimpleNamespace(
            selected_app_ids=tuple(sorted(selected | set(gpu_app_ids))),
            auto_enabled_app_ids=gpu_app_ids,
            issues=(),
        )

    monkeypatch.setattr("nebius_cxcli.cli.resolve_mk8s_gpu_app_selection", _fake_gpu_selection)

    rendered_messages: list[str] = []
    monkeypatch.setattr(
        "nebius_cxcli.cli.console.print",
        lambda *args, **_kwargs: rendered_messages.append(" ".join(str(arg) for arg in args)),
    )

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"cluster2"},
        selected_apps={f"{app_id}@cluster2" for app_id in existing_app_ids},
        infra_entries=(infra_entry,),
        app_entries=app_entries,
        provider_lookup=None,
    )

    assert completed is True
    assert not any(" on cluster1" in prompt for prompt in phase_prompts)
    for app_id in (*existing_app_ids, *gpu_app_ids):
        assert f"Configure '{app_id} on cluster2' component fields now?" in phase_prompts

    rendered = "\n".join(rendered_messages)
    assert "nebius-observability-agent on cluster2" in rendered
    assert "nvidia-gpu-operator on cluster2" in rendered
    assert "nebius-observability-agent on cluster1" not in rendered
    assert "nvidia-gpu-operator on cluster1" not in rendered

    payload = yaml.safe_load(updated_yaml)
    enabled_targets = sorted(
        (row["id"], row["instance_id"])
        for row in payload["apps"]["charts"]
        if isinstance(row, dict) and row.get("enabled") is True
    )
    assert enabled_targets == sorted(
        [
            *[
                (app_id, target_ref)
                for target_ref in ("cluster1", "cluster2")
                for app_id in existing_app_ids
            ],
            *[(app_id, "cluster2") for app_id in gpu_app_ids],
        ]
    )


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
                    },
                    {
                        "id": "mysterybox",
                        "instance_id": "mysterybox",
                        "enabled": True,
                        "inputs": {},
                    },
                ],
            },
            "apps": {
                "charts": [
                    {
                        "id": "demo-app",
                        "instance_id": "mk8s",
                        "enabled": True,
                        "repo": "oci://docker.io/example/demo-app",
                        "version": "1.0.0",
                        "namespace": "demo",
                        "release-name": "demo-app",
                        "values": {},
                    }
                ]
            },
            "deploy": {
                "targets": [
                    {
                        "instance_id": "mk8s",
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
        wizard_fields={
            "deploy.targets[].secrets.mysterybox.enabled": {
                "default": True,
                "write_default_to_config": True,
            },
            "deploy.targets[].secrets.mysterybox.allow_all_namespaces": {
                "default": True,
                "write_default_to_config": True,
            },
            "deploy.targets[].secrets.mysterybox.sync_namespaces": {
                "default": ["default"],
                "type_hint": "list(string)",
                "prompt_complex": True,
                "write_default_to_config": True,
                "required": True,
            },
        },
    )
    mysterybox_entry = ComponentEntry(
        id="mysterybox",
        scope="infra",
        config_path="infra.mysterybox",
        description="MysteryBox",
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
        lambda _source: (ModuleVariable(name="required_field", required=True, type_hint="string"),),
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
        if path_label == "infra.components[0].inputs.required_field":
            return "infra-value", False
        if path_label == "deploy.targets[0].secrets.mysterybox.enabled":
            assert current is True
            return True, False
        if path_label == "deploy.targets[0].secrets.mysterybox.allow_all_namespaces":
            assert current is True
            return True, False
        if path_label == "deploy.targets[0].secrets.mysterybox.sync_namespaces":
            assert current == ["default"]
            return ["app"], False
        if path_label == "apps.charts[0].values.image.tag":
            return "1.2.3", False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"mk8s", "mysterybox"},
        selected_apps={"demo-app@mk8s"},
        infra_entries=(infra_entry, mysterybox_entry),
        app_entries=(app_entry,),
        provider_lookup=None,
    )

    assert completed is True
    assert any("--- Infra wizard section ---" in message for message in rendered_messages)
    assert any("--- Apps wizard section ---" in message for message in rendered_messages)
    rendered_output = "\n".join(rendered_messages)
    assert (
        "[bold cyan]Wizard context:[/bold cyan] [dim]Current:[/dim] "
        "[bold magenta]Infra[/bold magenta] [dim]/[/dim] [bold green]mk8s[/bold green]"
        in rendered_output
    )
    assert (
        "[bold cyan]Wizard context:[/bold cyan] [dim]Current:[/dim] "
        "[bold magenta]Apps[/bold magenta] [dim]/[/dim] "
        "[bold green]demo-app on mk8s[/bold green]" in rendered_output
    )
    assert (
        "[bold cyan]Wizard context:[/bold cyan] [dim]Current:[/dim] "
        "[bold magenta]Deploy Target[/bold magenta] [dim]/[/dim] "
        "[bold green]mk8s / MysteryBox ESO sync[/bold green]" in rendered_output
    )
    assert "Selected deploy.targets[0].secrets.mysterybox.enabled = true" in rendered_output
    assert (
        "Selected deploy.targets[0].secrets.mysterybox.allow_all_namespaces = true"
        in rendered_output
    )
    assert (
        'Selected deploy.targets[0].secrets.mysterybox.sync_namespaces = ["app"]' in rendered_output
    )
    updated_payload = yaml.safe_load(updated_yaml)
    assert updated_payload["deploy"]["targets"][0]["secrets"]["mysterybox"]["enabled"] is True
    assert (
        updated_payload["deploy"]["targets"][0]["secrets"]["mysterybox"]["allow_all_namespaces"]
        is True
    )
    assert updated_payload["deploy"]["targets"][0]["secrets"]["mysterybox"]["sync_namespaces"] == [
        "app"
    ]
    assert "    * mk8s (current)" not in rendered_output
    assert "    * demo-app on mk8s (current)" not in rendered_output
    assert any(
        "Selected infra.components[0].inputs.required_field = infra-value" in message
        for message in rendered_messages
    )
    assert any(
        "Selected apps.charts[0].values.image.tag = 1.2.3" in message
        for message in rendered_messages
    )


def test_vm_preemptible_wizard_sets_required_recovery_policy(
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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


def test_vm_service_account_prompt_is_hidden_for_built_in_observability(
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
                        "logs": {"enabled": True, "systemd_units": []},
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
            "deploy.observability.vm.logs.enabled": {},
            "inputs.service_account_id": {"prompt": False},
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
        prompted_paths.append(path_label)
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
    assert not any(path.endswith(".inputs.service_account_id") for path in prompted_paths)
    payload = yaml.safe_load(updated_yaml)
    assert "service_account_id" not in payload["infra"]["components"][0]["inputs"]


def test_vm_observability_true_prompts_builtin_journald_logs_only(
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
                    "enabled": False,
                    "vm": {
                        "logs": {"enabled": True, "systemd_units": []},
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
            "deploy.observability.vm.logs.enabled": {},
            "inputs.service_account_id": {"prompt": False},
        },
    )

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    prompted: dict[str, object] = {}

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
        prompted[path_label] = current
        if path_label == "deploy.observability.enabled":
            return True, False
        return current, False

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
    assert prompted["deploy.observability.vm.logs.enabled"] is True
    assert "infra.components[0].inputs.service_account_id" not in prompted
    payload = yaml.safe_load(updated_yaml)
    assert payload["deploy"]["observability"]["enabled"] is True
    assert payload["deploy"]["observability"]["vm"]["logs"]["enabled"] is True
    assert "collector" not in payload["deploy"]["observability"]["vm"]
    assert "service_account_id" not in payload["infra"]["components"][0]["inputs"]


def test_vm_wizard_prefills_boot_disk_size_after_preset_selection(monkeypatch) -> None:
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
                        "id": "vm",
                        "instance_id": "vm",
                        "enabled": True,
                        "source": "../../platform-infra/modules/vm",
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
        source="../../platform-infra/modules/vm",
        wizard_fields={
            "inputs.data_disk_enabled": {"write_default_to_config": True},
            "inputs.data_disk_type": {
                "options": {
                    "from": "compute_boot_disk_types",
                    "auto_select_first": True,
                },
                "write_default_to_config": True,
            },
            "inputs.data_disk_size_gib": {"write_default_to_config": True},
        },
    )

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: (
            "parent_id",
            "network_id",
            "subnet_id",
            "name",
            "platform",
            "preset",
            "source_image_family",
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=True, type_hint="string"),
            ModuleVariable(name="network_id", required=True, type_hint="string"),
            ModuleVariable(name="subnet_id", required=True, type_hint="string"),
            ModuleVariable(name="name", required=True, type_hint="string"),
            ModuleVariable(name="platform", required=True, type_hint="string"),
            ModuleVariable(name="preset", required=True, type_hint="string"),
            ModuleVariable(name="source_image_family", required=True, type_hint="string"),
            ModuleVariable(
                name="boot_disk_type",
                required=False,
                type_hint="string",
                has_default=True,
                default="NETWORK_SSD",
            ),
            ModuleVariable(
                name="boot_disk_encryption_enabled",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
            ModuleVariable(
                name="boot_disk_deletion_protection",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
            ModuleVariable(
                name="boot_disk_size_gib",
                required=False,
                type_hint="number",
                has_default=True,
                default=None,
            ),
            ModuleVariable(
                name="data_disk_enabled",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
            ModuleVariable(
                name="data_disk_type",
                required=False,
                type_hint="string",
                has_default=True,
                default="NETWORK_SSD",
            ),
            ModuleVariable(
                name="data_disk_size_gib",
                required=False,
                type_hint="number",
                has_default=True,
                default=128,
            ),
        ),
    )

    prompted_defaults: dict[str, object] = {}
    prompted_paths: list[str] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
        prompted_defaults[path_label] = current
        prompted_paths.append(path_label)
        if path_label.endswith(".subnet_id"):
            return "subnet-1", False
        if path_label.endswith(".platform"):
            return "cpu-d3", False
        if path_label.endswith(".preset"):
            return "4vcpu-16gb", False
        if path_label.endswith(".source_image_family"):
            return "ubuntu24.04-driverless", False
        if path_label.endswith(".name"):
            return "demo-vm", False
        return current, False

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
    boot_type_path = "infra.components[0].inputs.boot_disk_type"
    boot_encryption_path = "infra.components[0].inputs.boot_disk_encryption_enabled"
    boot_deletion_path = "infra.components[0].inputs.boot_disk_deletion_protection"
    boot_size_path = "infra.components[0].inputs.boot_disk_size_gib"
    data_enabled_path = "infra.components[0].inputs.data_disk_enabled"
    data_type_path = "infra.components[0].inputs.data_disk_type"
    data_size_path = "infra.components[0].inputs.data_disk_size_gib"
    assert prompted_paths.index(boot_type_path) < prompted_paths.index(boot_size_path)
    assert prompted_defaults[boot_type_path] == "NETWORK_SSD"
    assert boot_encryption_path not in prompted_paths
    assert prompted_defaults[boot_deletion_path] is False
    assert prompted_defaults[boot_size_path] == 64
    assert prompted_defaults[data_enabled_path] is False
    assert data_type_path not in prompted_paths
    assert data_size_path not in prompted_paths
    payload = yaml.safe_load(updated_yaml)
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["boot_disk_type"] == "NETWORK_SSD"
    assert "boot_disk_encryption_enabled" not in inputs
    assert inputs.get("boot_disk_deletion_protection") in (None, False)
    assert inputs["boot_disk_size_gib"] == 64
    assert inputs["data_disk_enabled"] is False
    assert "data_disk_type" not in inputs
    assert "data_disk_size_gib" not in inputs


def test_vm_wizard_prompts_secondary_data_disk_shape_when_enabled(monkeypatch) -> None:
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
                        "id": "vm",
                        "instance_id": "vm",
                        "enabled": True,
                        "source": "../../platform-infra/modules/vm",
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
        source="../../platform-infra/modules/vm",
        wizard_fields={
            "inputs.data_disk_enabled": {"write_default_to_config": True},
            "inputs.data_disk_type": {
                "options": {
                    "from": "compute_boot_disk_types",
                    "auto_select_first": True,
                },
                "write_default_to_config": True,
            },
            "inputs.data_disk_size_gib": {"write_default_to_config": True},
        },
    )

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: (
            "parent_id",
            "network_id",
            "subnet_id",
            "name",
            "platform",
            "preset",
            "source_image_family",
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=True, type_hint="string"),
            ModuleVariable(name="subnet_id", required=True, type_hint="string"),
            ModuleVariable(name="name", required=True, type_hint="string"),
            ModuleVariable(name="platform", required=True, type_hint="string"),
            ModuleVariable(name="preset", required=True, type_hint="string"),
            ModuleVariable(name="source_image_family", required=True, type_hint="string"),
            ModuleVariable(
                name="boot_disk_type",
                required=False,
                type_hint="string",
                has_default=True,
                default="NETWORK_SSD",
            ),
            ModuleVariable(
                name="boot_disk_deletion_protection",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
            ModuleVariable(
                name="boot_disk_size_gib",
                required=False,
                type_hint="number",
                has_default=True,
                default=None,
            ),
            ModuleVariable(
                name="data_disk_enabled",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
            ModuleVariable(
                name="data_disk_type",
                required=False,
                type_hint="string",
                has_default=True,
                default="NETWORK_SSD",
            ),
            ModuleVariable(
                name="data_disk_encryption_enabled",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
            ModuleVariable(
                name="data_disk_deletion_protection",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
            ModuleVariable(
                name="data_disk_size_gib",
                required=False,
                type_hint="number",
                has_default=True,
                default=128,
            ),
        ),
    )

    prompted_defaults: dict[str, object] = {}
    prompted_paths: list[str] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
        prompted_defaults[path_label] = current
        prompted_paths.append(path_label)
        if path_label.endswith(".subnet_id"):
            return "subnet-1", False
        if path_label.endswith(".platform"):
            return "cpu-d3", False
        if path_label.endswith(".preset"):
            return "4vcpu-16gb", False
        if path_label.endswith(".source_image_family"):
            return "ubuntu24.04-driverless", False
        if path_label.endswith(".name"):
            return "demo-vm", False
        if path_label.endswith(".data_disk_enabled"):
            return True, False
        if path_label.endswith(".data_disk_type"):
            return "NETWORK_SSD_NON_REPLICATED", False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"vm"},
        selected_apps=set(),
        infra_entries=(vm_entry,),
        app_entries=(),
        provider_lookup=None,
    )

    data_enabled_path = "infra.components[0].inputs.data_disk_enabled"
    data_type_path = "infra.components[0].inputs.data_disk_type"
    data_encryption_path = "infra.components[0].inputs.data_disk_encryption_enabled"
    data_deletion_path = "infra.components[0].inputs.data_disk_deletion_protection"
    data_size_path = "infra.components[0].inputs.data_disk_size_gib"
    assert completed is True
    assert prompted_paths.index(data_enabled_path) < prompted_paths.index(data_type_path)
    assert prompted_paths.index(data_type_path) < prompted_paths.index(data_size_path)
    assert prompted_defaults[data_enabled_path] is False
    assert prompted_defaults[data_type_path] == "NETWORK_SSD"
    assert prompted_paths.index(data_type_path) < prompted_paths.index(data_encryption_path)
    assert prompted_defaults[data_encryption_path] is False
    assert prompted_defaults[data_deletion_path] is False
    assert prompted_defaults[data_size_path] == 186
    payload = yaml.safe_load(updated_yaml)
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["data_disk_enabled"] is True
    assert inputs["data_disk_type"] == "NETWORK_SSD_NON_REPLICATED"
    assert "data_disk_encryption_enabled" not in inputs
    assert inputs.get("data_disk_deletion_protection") in (None, False)
    assert inputs["data_disk_size_gib"] == 186


@pytest.mark.parametrize(
    ("component_id", "description"),
    [
        ("wireguard-gw", "WireGuard VPN gateway"),
        ("ssh-jumphost", "SSH jump host"),
    ],
)
def test_jump_host_wizard_prefills_boot_disk_size_and_skips_created_public_ip_id(
    monkeypatch,
    component_id: str,
    description: str,
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
                "notifications": {"email_enabled": False, "email": None},
            },
            "infra": {
                "components": [
                    {
                        "id": component_id,
                        "instance_id": component_id,
                        "enabled": True,
                        "source": f"../../platform-infra/modules/{component_id}",
                        "inputs": {},
                    }
                ]
            },
            "apps": {"charts": []},
        },
        sort_keys=False,
    )

    entry = ComponentEntry(
        id=component_id,
        scope="infra",
        config_path=f"infra.components.{component_id}",
        description=description,
        source=f"../../platform-infra/modules/{component_id}",
        wizard_fields=(
            {"inputs.wireguard_tunnel_cidr": {"write_default_to_config": True}}
            if component_id == "wireguard-gw"
            else {}
        ),
    )

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: (
            "parent_id",
            "subnet_id",
            "name",
            "platform",
            "preset",
            "source_image_family",
            "boot_disk_size_gib",
        ),
    )

    def _module_variables(_source: str) -> tuple[ModuleVariable, ...]:
        variables = (
            ModuleVariable(name="parent_id", required=True, type_hint="string"),
            ModuleVariable(name="network_id", required=True, type_hint="string"),
            ModuleVariable(name="subnet_id", required=True, type_hint="string"),
            ModuleVariable(name="name", required=True, type_hint="string"),
            ModuleVariable(name="platform", required=True, type_hint="string"),
            ModuleVariable(name="preset", required=True, type_hint="string"),
            ModuleVariable(name="source_image_family", required=True, type_hint="string"),
            ModuleVariable(name="boot_disk_size_gib", required=True, type_hint="number"),
            ModuleVariable(
                name="boot_disk_type",
                required=False,
                type_hint="string",
                has_default=True,
                default="NETWORK_SSD",
            ),
            ModuleVariable(
                name="boot_disk_encryption_enabled",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
            ModuleVariable(
                name="boot_disk_deletion_protection",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
            ModuleVariable(
                name="create_public_ip_allocation",
                required=False,
                type_hint="bool",
                has_default=True,
                default=True,
            ),
            ModuleVariable(
                name="public_ip_allocation_id",
                required=False,
                type_hint="string",
                has_default=True,
                default=None,
            ),
            ModuleVariable(
                name="public_ip_allocation_name",
                required=False,
                type_hint="string",
                has_default=True,
                default=None,
            ),
        )
        if component_id != "wireguard-gw":
            return variables
        return variables + (
            ModuleVariable(
                name="wireguard_tunnel_cidr",
                required=False,
                type_hint="string",
                has_default=True,
                default="10.8.0.1/22",
            ),
        )

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", _module_variables)

    prompted_defaults: dict[str, object] = {}
    prompted_paths: list[str] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
        prompted_defaults[path_label] = current
        prompted_paths.append(path_label)
        if path_label.endswith(".subnet_id"):
            return "subnet-1", False
        if path_label.endswith(".name"):
            return "wg", False
        if path_label.endswith(".platform"):
            return "cpu-d3", False
        if path_label.endswith(".preset"):
            return "4vcpu-16gb", False
        if path_label.endswith(".source_image_family"):
            return "ubuntu24.04-driverless", False
        if path_label.endswith(".create_public_ip_allocation"):
            return True, False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={component_id},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    boot_encryption_path = "infra.components[0].inputs.boot_disk_encryption_enabled"
    boot_deletion_path = "infra.components[0].inputs.boot_disk_deletion_protection"
    boot_size_path = "infra.components[0].inputs.boot_disk_size_gib"
    assert boot_encryption_path not in prompted_paths
    assert prompted_defaults[boot_deletion_path] is False
    assert prompted_defaults[boot_size_path] == 64
    assert "infra.components[0].inputs.public_ip_allocation_id" not in prompted_paths
    payload = yaml.safe_load(updated_yaml)
    inputs = payload["infra"]["components"][0]["inputs"]
    assert "boot_disk_encryption_enabled" not in inputs
    assert inputs.get("boot_disk_deletion_protection") in (None, False)
    assert inputs["boot_disk_size_gib"] == 64
    assert prompted_defaults["infra.components[0].inputs.create_public_ip_allocation"] is True
    assert "public_ip_allocation_id" not in inputs
    tunnel_cidr_path = "infra.components[0].inputs.wireguard_tunnel_cidr"
    if component_id == "wireguard-gw":
        assert prompted_defaults[tunnel_cidr_path] == "10.8.0.1/22"
        assert inputs["wireguard_tunnel_cidr"] == "10.8.0.1/22"
    else:
        assert tunnel_cidr_path not in prompted_paths


@pytest.mark.parametrize(
    ("component_id", "description"),
    [
        ("vm", "VM"),
        ("wireguard-gw", "WireGuard VPN gateway"),
        ("ssh-jumphost", "SSH jump host"),
        ("nfs", "NFS server"),
    ],
)
def test_vm_style_wizard_prompts_boot_disk_encryption_for_supported_disk_types(
    monkeypatch,
    component_id: str,
    description: str,
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
                "notifications": {"email_enabled": False, "email": None},
            },
            "infra": {
                "components": [
                    {
                        "id": component_id,
                        "instance_id": component_id,
                        "enabled": True,
                        "source": f"../../platform-infra/modules/{component_id}",
                        "inputs": {},
                    }
                ]
            },
            "apps": {"charts": []},
        },
        sort_keys=False,
    )

    entry = ComponentEntry(
        id=component_id,
        scope="infra",
        config_path=f"infra.components.{component_id}",
        description=description,
        source=f"../../platform-infra/modules/{component_id}",
    )

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: (
            "parent_id",
            "subnet_id",
            "name",
            "platform",
            "preset",
            "source_image_family",
            "boot_disk_size_gib",
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=True, type_hint="string"),
            ModuleVariable(name="subnet_id", required=True, type_hint="string"),
            ModuleVariable(name="name", required=True, type_hint="string"),
            ModuleVariable(name="platform", required=True, type_hint="string"),
            ModuleVariable(name="preset", required=True, type_hint="string"),
            ModuleVariable(name="source_image_family", required=True, type_hint="string"),
            ModuleVariable(name="boot_disk_size_gib", required=True, type_hint="number"),
            ModuleVariable(
                name="boot_disk_type",
                required=False,
                type_hint="string",
                has_default=True,
                default="NETWORK_SSD",
            ),
            ModuleVariable(
                name="boot_disk_encryption_enabled",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
            ModuleVariable(
                name="boot_disk_deletion_protection",
                required=False,
                type_hint="bool",
                has_default=True,
                default=False,
            ),
        ),
    )

    prompted_defaults: dict[str, object] = {}
    prompted_paths: list[str] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
        prompted_defaults[path_label] = current
        prompted_paths.append(path_label)
        if path_label.endswith(".subnet_id"):
            return "subnet-1", False
        if path_label.endswith(".name"):
            return "vm", False
        if path_label.endswith(".platform"):
            return "cpu-d3", False
        if path_label.endswith(".preset"):
            return "4vcpu-16gb", False
        if path_label.endswith(".source_image_family"):
            return "ubuntu24.04-driverless", False
        if path_label.endswith(".boot_disk_type"):
            return "NETWORK_SSD_NON_REPLICATED", False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={component_id},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=None,
    )

    boot_type_path = "infra.components[0].inputs.boot_disk_type"
    boot_encryption_path = "infra.components[0].inputs.boot_disk_encryption_enabled"
    boot_deletion_path = "infra.components[0].inputs.boot_disk_deletion_protection"
    assert completed is True
    assert prompted_paths.index(boot_type_path) < prompted_paths.index(boot_encryption_path)
    assert prompted_defaults[boot_encryption_path] is False
    assert prompted_defaults[boot_deletion_path] is False
    payload = yaml.safe_load(updated_yaml)
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["boot_disk_type"] == "NETWORK_SSD_NON_REPLICATED"
    assert inputs.get("boot_disk_encryption_enabled") in (None, False)
    assert inputs.get("boot_disk_deletion_protection") in (None, False)


@pytest.mark.parametrize(
    ("component_id", "description"),
    [
        ("wireguard-gw", "WireGuard VPN gateway"),
        ("ssh-jumphost", "SSH jump host"),
    ],
)
def test_jump_host_wizard_requires_existing_public_ip_id_when_create_is_false(
    monkeypatch,
    component_id: str,
    description: str,
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
                "notifications": {"email_enabled": False, "email": None},
            },
            "infra": {
                "components": [
                    {
                        "id": component_id,
                        "instance_id": component_id,
                        "enabled": True,
                        "source": f"../../platform-infra/modules/{component_id}",
                        "inputs": {},
                    }
                ]
            },
            "apps": {"charts": []},
        },
        sort_keys=False,
    )

    entry = ComponentEntry(
        id=component_id,
        scope="infra",
        config_path=f"infra.components.{component_id}",
        description=description,
        source=f"../../platform-infra/modules/{component_id}",
    )

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("parent_id", "network_id", "subnet_id", "name", "platform", "preset"),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (
            ModuleVariable(name="parent_id", required=True, type_hint="string"),
            ModuleVariable(name="network_id", required=True, type_hint="string"),
            ModuleVariable(name="subnet_id", required=True, type_hint="string"),
            ModuleVariable(name="name", required=True, type_hint="string"),
            ModuleVariable(name="platform", required=True, type_hint="string"),
            ModuleVariable(name="preset", required=True, type_hint="string"),
            ModuleVariable(
                name="create_public_ip_allocation",
                required=False,
                type_hint="bool",
                has_default=True,
                default=True,
            ),
            ModuleVariable(
                name="public_ip_allocation_id",
                required=False,
                type_hint="string",
                has_default=True,
                default=None,
            ),
            ModuleVariable(
                name="public_ip_allocation_name",
                required=False,
                type_hint="string",
                has_default=True,
                default=None,
            ),
        ),
    )

    prompted_required: dict[str, bool] = {}
    prompted_paths: list[str] = []

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint
        prompted_required[path_label] = required
        prompted_paths.append(path_label)
        if path_label.endswith(".subnet_id"):
            return "subnet-1", False
        if path_label.endswith(".name"):
            return "wg", False
        if path_label.endswith(".platform"):
            return "cpu-d3", False
        if path_label.endswith(".preset"):
            return "4vcpu-16gb", False
        if path_label.endswith(".create_public_ip_allocation"):
            return False, False
        if path_label.endswith(".public_ip_allocation_id"):
            return "allocation-1", False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _fake_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={component_id},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=None,
    )

    public_id_path = "infra.components[0].inputs.public_ip_allocation_id"
    assert completed is True
    assert public_id_path in prompted_paths
    assert prompted_required[public_id_path] is True
    assert "infra.components[0].inputs.public_ip_allocation_name" not in prompted_paths
    payload = yaml.safe_load(updated_yaml)
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["create_public_ip_allocation"] is False
    assert inputs["public_ip_allocation_id"] == "allocation-1"


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
    monkeypatch.setattr("nebius_cxcli.cli._app_chart_default_values", lambda **_kwargs: {})

    prompted_paths: list[str] = []
    prompt_counts: dict[str, int] = {}

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
        prompted_paths.append(path_label)
        prompt_counts[path_label] = prompt_counts.get(path_label, 0) + 1
        if path_label.endswith(".cluster_name"):
            return "cluster1", False
        if path_label.endswith(".gpu_enabled"):
            return prompt_counts[path_label] > 1, False
        if path_label.endswith(".cpu_nodes_boot_disk_size_gib"):
            if prompt_counts[path_label] == 1:
                return cli._WIZARD_BACKTRACK, False
            return 93, False
        if path_label.endswith(".cpu_nodes_boot_disk_type"):
            return "NETWORK_SSD", False
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
        "infra.components[0].inputs.cpu_nodes_boot_disk_size_gib",
        "infra.components[0].inputs.gpu_enabled",
        "infra.components[0].inputs.cpu_nodes_boot_disk_size_gib",
        "infra.components[0].inputs.cpu_nodes_boot_disk_type",
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
        prompted.append((path_label, current))
        return current, False

    class _Lookup(ProviderOptionLookup):
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

    class _Lookup(ProviderOptionLookup):
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

    class _Lookup(ProviderOptionLookup):
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


def test_materialize_singleton_provider_defaults_sets_hidden_mk8s_gpu_fabric() -> None:
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
                        "node_group_defaults": {
                            "gpu": {
                                "platform": "gpu-h100-sxm",
                                "preset": "8gpu-128vcpu-1600gb",
                            }
                        },
                        "gpu_clusters": {"workers": {}},
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
            "inputs.gpu_clusters.workers.infiniband_fabric": {
                "prompt": False,
                "options": {
                    "from": "mk8s_infiniband_fabrics",
                    "args": {
                        "platform_path": "inputs.node_group_defaults.gpu.platform",
                        "preset_path": "inputs.node_group_defaults.gpu.preset",
                    },
                    "auto_select_first": True,
                    "skip_prompt_if_no_choices": True,
                },
            }
        },
    )

    class _Lookup(ProviderOptionLookup):
        def resolve(self, *, provider, args, payload, field_path):
            _ = args, payload
            if provider == "mk8s_infiniband_fabrics" and field_path.endswith(
                ".gpu_clusters.workers.infiniband_fabric"
            ):
                return [
                    OptionChoice(value="fabric-2", label="fabric-2", recommended=True),
                    OptionChoice(value="fabric-3", label="fabric-3"),
                ]
            return []

        def last_error(self):
            return None

    _materialize_singleton_provider_defaults(
        payload=payload,
        selected_infra={"mk8s"},
        infra_entries=(entry,),
        provider_lookup=_Lookup(),
    )

    assert (
        payload["infra"]["components"][0]["inputs"]["gpu_clusters"]["workers"]["infiniband_fabric"]
        == "fabric-2"
    )


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

    class _Lookup(ProviderOptionLookup):
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
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


def test_wizard_writes_declared_default_when_config_write_is_enabled(
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
                "write_default_to_config": True,
            }
        },
    )

    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: (),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    prompt_defaults: list[object] = []

    def _fake_typer_prompt(_prompt_text: str, *, default: object = "", **_kwargs) -> object:
        prompt_defaults.append(default)
        return default

    monkeypatch.setattr("nebius_cxcli.cli.typer.prompt", _fake_typer_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=config_yaml,
        selected_infra={"demo-module"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert prompt_defaults == ["false"]
    payload = yaml.safe_load(updated_yaml)
    assert payload["infra"]["components"][0]["inputs"]["demo_toggle_group"] == {"enabled": False}


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
                            "node_groups": {
                                "worker": {
                                    "gpu": True,
                                    "platform": "gpu-h100-sxm",
                                    "preset": "8gpu-128vcpu-1600gb",
                                    "node_count": 1,
                                }
                            },
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
        lambda _source: (),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._runtime_required_input_leaf_names",
        lambda _entry: set(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_variables",
        lambda _source: (),
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
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

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = allow_back
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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

    def _capture_continue_phase(label: str, *, default: bool = True, allow_back: bool = False):
        _ = allow_back
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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
            "inputs.cluster.k8s_version": {
                "options": {
                    "from": "mk8s_control_plane_versions",
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
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
        "infra.components[0].inputs.cluster.k8s_version",
        "infra.components[0].inputs.gpu_enabled",
        "infra.components[0].inputs.gpu_node_groups",
        "infra.components[0].inputs.gpu_nodes_count_per_group",
        "infra.components[0].inputs.gpu_nodes_platform",
        "infra.components[0].inputs.gpu_nodes_preset",
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
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

    class _Lookup(ProviderOptionLookup):
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
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = current, choices, type_hint, required, unset_on_skip
        prompted_paths.append(path_label)
        if path_label.endswith(".gpu_enabled"):
            return True, False
        if path_label.endswith(".gpu_nodes_platform"):
            return "gpu-b200-sxm", False
        if path_label.endswith(".gpu_nodes_preset"):
            return "1gpu-20vcpu-224gb", False
        return current, False

    class _Lookup(ProviderOptionLookup):
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
                        "instance_id": "mk8s",
                        "enabled": True,
                        "source": "../../platform-infra/modules/mk8s",
                        "inputs": {
                            "node_group_defaults": {
                                "gpu": {
                                    "platform": "gpu-b200-sxm",
                                    "preset": "8gpu-160vcpu-1792gb",
                                }
                            },
                            "gpu_clusters": {
                                "workers": {
                                    "infiniband_fabric": "us-central1-b",
                                }
                            },
                        },
                    }
                ],
            },
            "apps": {
                "charts": [
                    {
                        "id": "soperator",
                        "instance_id": "mk8s",
                        "enabled": True,
                        "install_mode": "production-cluster",
                        "values": {},
                    }
                ]
            },
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
            "inputs.node_group_defaults.gpu.preset": {
                "options": {
                    "from": "compute_platform_presets",
                    "args": {
                        "platform_path": "inputs.node_group_defaults.gpu.platform",
                        "reservation_policy_path": (
                            "inputs.node_group_defaults.gpu.reservation.policy"
                        ),
                    },
                }
            },
            "inputs.gpu_clusters.workers.infiniband_fabric": {
                "options": {
                    "from": "mk8s_infiniband_fabrics",
                    "args": {
                        "platform_path": "inputs.node_group_defaults.gpu.platform",
                        "preset_path": "inputs.node_group_defaults.gpu.preset",
                        "reservation_policy_path": (
                            "inputs.node_group_defaults.gpu.reservation.policy"
                        ),
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
        lambda _source: (),
    )
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", lambda *_args, **_kwargs: True)

    def _fake_prompt(
        path_label: str,
        current: object,
        *,
        choices=None,
        type_hint=None,
        required=False,
        unset_on_skip=False,
    ) -> tuple[object, bool]:
        _ = choices, type_hint, required, unset_on_skip
        if path_label.endswith(".preset"):
            return "1gpu-20vcpu-224gb", False
        return current, False

    class _Lookup(ProviderOptionLookup):
        def resolve(self, *, provider, args, payload, field_path):
            _ = args, payload
            if provider == "compute_platform_presets" and field_path.endswith(".preset"):
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
    gpu_defaults = inputs["node_group_defaults"]["gpu"]
    assert gpu_defaults["preset"] == "1gpu-20vcpu-224gb"
    assert "infiniband_fabric" not in gpu_defaults
    assert "gpu_clusters" not in inputs or "infiniband_fabric" not in inputs["gpu_clusters"].get(
        "workers", {}
    )
