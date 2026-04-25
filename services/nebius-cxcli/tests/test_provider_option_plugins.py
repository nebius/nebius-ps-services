from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from nebius_cxcli.provider_options import OptionChoice, ProviderOptionLookup


def _install_module(monkeypatch, name: str, module: ModuleType) -> None:
    parts = name.split(".")
    for index in range(1, len(parts)):
        package_name = ".".join(parts[:index])
        package = sys.modules.get(package_name)
        if package is None:
            package = ModuleType(package_name)
            package.__path__ = []  # type: ignore[attr-defined]
            monkeypatch.setitem(sys.modules, package_name, package)
        if index > 1:
            parent_name = ".".join(parts[: index - 1])
            setattr(sys.modules[parent_name], parts[index - 1], package)
    monkeypatch.setitem(sys.modules, name, module)
    if len(parts) > 1:
        parent_name = ".".join(parts[:-1])
        setattr(sys.modules[parent_name], parts[-1], module)


def _install_fake_mk8s_module(
    monkeypatch,
    *,
    compatible_platforms: list[str] | None = None,
    compatibility_items: list[dict[str, object]] | None = None,
) -> None:
    mk8s_module = ModuleType("nebius.api.nebius.mk8s.v1")

    class GetNodeGroupCompatibilityMatrixRequest:
        def __init__(self, *, cluster_kubernetes_version: str) -> None:
            self.cluster_kubernetes_version = cluster_kubernetes_version

    class NodeGroupServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_compatibility_matrix(self, request: object) -> SimpleNamespace:
            _ = request
            if compatibility_items is not None:
                items = [
                    SimpleNamespace(
                        compatible_platforms=list(item.get("compatible_platforms", [])),
                        drivers_preset=item.get("drivers_preset"),
                        os=item.get("os"),
                    )
                    for item in compatibility_items
                ]
            else:
                items = [
                    SimpleNamespace(compatible_platforms=list(compatible_platforms or [])),
                ]
            response = SimpleNamespace(
                versions=[
                    SimpleNamespace(
                        items=items,
                    )
                ]
            )
            return SimpleNamespace(wait=lambda: response)

    mk8s_module.GetNodeGroupCompatibilityMatrixRequest = GetNodeGroupCompatibilityMatrixRequest
    mk8s_module.NodeGroupServiceClient = NodeGroupServiceClient
    _install_module(monkeypatch, "nebius.api.nebius.mk8s.v1", mk8s_module)


def _install_fake_compute_module(
    monkeypatch,
    *,
    platforms: list[tuple[str, str | None]],
    presets_by_platform: dict[str, list[dict[str, object]]] | None = None,
    public_images: list[dict[str, object]] | None = None,
) -> None:
    common_module = ModuleType("nebius.api.nebius.common.v1")

    class GetByNameRequest:
        def __init__(self, *, parent_id: str, name: str) -> None:
            self.parent_id = parent_id
            self.name = name

    common_module.GetByNameRequest = GetByNameRequest
    _install_module(monkeypatch, "nebius.api.nebius.common.v1", common_module)

    compute_module = ModuleType("nebius.api.nebius.compute.v1")

    class ListPlatformsRequest:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    class ListPublicRequest:
        def __init__(self, *, region: str, page_size: int, page_token: str) -> None:
            self.region = region
            self.page_size = page_size
            self.page_token = page_token

    class PlatformServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def list(self, request: object) -> SimpleNamespace:
            _ = request
            response = SimpleNamespace(
                items=[
                    SimpleNamespace(
                        metadata=SimpleNamespace(name=name),
                        spec=SimpleNamespace(short_human_readable_name=short_name),
                    )
                    for name, short_name in platforms
                ],
                next_page_token="",
            )
            return SimpleNamespace(wait=lambda: response)

        def get_by_name(self, request: object) -> SimpleNamespace:
            platform_name = getattr(request, "name", "")
            presets = [
                SimpleNamespace(
                    name=item.get("name"),
                    resources=SimpleNamespace(
                        vcpu_count=item.get("vcpu_count"),
                        memory_gibibytes=item.get("memory_gibibytes"),
                        gpu_count=item.get("gpu_count"),
                    ),
                    allow_gpu_clustering=bool(item.get("allow_gpu_clustering", False)),
                )
                for item in (presets_by_platform or {}).get(platform_name, [])
            ]
            response = SimpleNamespace(
                spec=SimpleNamespace(
                    presets=presets,
                )
            )
            return SimpleNamespace(wait=lambda: response)

    class ImageServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def list_public(self, request: object) -> SimpleNamespace:
            _ = request
            response = SimpleNamespace(
                items=[
                    SimpleNamespace(
                        spec=SimpleNamespace(
                            image_family=item.get("image_family"),
                            image_family_human_readable=item.get("image_family_human_readable"),
                            recommended_platforms=list(item.get("recommended_platforms", [])),
                            unsupported_platforms=[
                                SimpleNamespace(key=key, value=value)
                                for key, value in dict(
                                    item.get("unsupported_platforms", {})
                                ).items()
                            ],
                        )
                    )
                    for item in (public_images or [])
                ],
                next_page_token="",
            )
            return SimpleNamespace(wait=lambda: response)

    compute_module.ListPlatformsRequest = ListPlatformsRequest
    compute_module.ListPublicRequest = ListPublicRequest
    compute_module.PlatformServiceClient = PlatformServiceClient
    compute_module.ImageServiceClient = ImageServiceClient
    _install_module(monkeypatch, "nebius.api.nebius.compute.v1", compute_module)


def _install_fake_capacity_module(
    monkeypatch,
    *,
    resource_advice_items: list[dict[str, object]],
) -> None:
    capacity_module = ModuleType("nebius.api.nebius.capacity.v1")

    class ListResourceAdviceRequest:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    class ResourceAdviceServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def list(self, request: object) -> SimpleNamespace:
            _ = request
            response = SimpleNamespace(
                items=[
                    SimpleNamespace(
                        spec=SimpleNamespace(
                            region=item.get("region"),
                            fabric=item.get("fabric", ""),
                            compute_instance=SimpleNamespace(
                                platform=item.get("platform"),
                                preset=SimpleNamespace(name=item.get("preset")),
                            ),
                        ),
                        status=SimpleNamespace(
                            on_demand=SimpleNamespace(
                                available=item.get("on_demand_available", 0),
                                limit=item.get("on_demand_limit", 0),
                                availability_level=SimpleNamespace(
                                    name=item.get(
                                        "on_demand_level",
                                        "AVAILABILITY_LEVEL_UNKNOWN",
                                    )
                                ),
                                data_state=SimpleNamespace(
                                    name=item.get("on_demand_state", "DATA_STATE_FRESH")
                                ),
                            ),
                            reserved=SimpleNamespace(
                                available=item.get("reserved_available", 0),
                                limit=item.get("reserved_limit", 0),
                                availability_level=SimpleNamespace(
                                    name=item.get(
                                        "reserved_level",
                                        "AVAILABILITY_LEVEL_UNKNOWN",
                                    )
                                ),
                                data_state=SimpleNamespace(
                                    name=item.get("reserved_state", "DATA_STATE_FRESH")
                                ),
                            ),
                        ),
                    )
                    for item in resource_advice_items
                ],
                next_page_token="",
            )
            return SimpleNamespace(wait=lambda: response)

    capacity_module.ListResourceAdviceRequest = ListResourceAdviceRequest
    capacity_module.ResourceAdviceServiceClient = ResourceAdviceServiceClient
    _install_module(monkeypatch, "nebius.api.nebius.capacity.v1", capacity_module)


def test_provider_option_lookup_uses_plugin_for_unknown_provider(
    monkeypatch,
) -> None:
    def _plugin(**kwargs):
        if kwargs.get("provider") != "vendor_regions":
            return []
        return [{"value": "us-central1", "label": "US Central 1"}]

    monkeypatch.setenv(
        "NEBIUS_CXCLI_PROVIDER_OPTION_PLUGINS",
        "tests.test_provider_option_plugins:_plugin",
    )
    monkeypatch.setattr(
        "nebius_cxcli.provider_options._load_option_plugins",
        lambda _specs: (_plugin,),
    )

    lookup = ProviderOptionLookup()
    resolved = lookup.resolve(
        provider="vendor_regions",
        args={},
        payload={},
        field_path="client_info.nebius.region_id",
    )
    assert [choice.value for choice in resolved] == ["us-central1"]
    assert [choice.label for choice in resolved] == ["US Central 1"]


def test_provider_option_lookup_applies_filter_regex_to_plugin_choices(
    monkeypatch,
) -> None:
    def _plugin(**kwargs):
        if kwargs.get("provider") != "vendor_networks":
            return []
        return [
            {"value": "vpcnetwork-prod-a", "label": "Prod"},
            {"value": "vpcnetwork-dev-a", "label": "Dev"},
        ]

    monkeypatch.setenv(
        "NEBIUS_CXCLI_PROVIDER_OPTION_PLUGINS",
        "tests.test_provider_option_plugins:_plugin",
    )
    monkeypatch.setattr(
        "nebius_cxcli.provider_options._load_option_plugins",
        lambda _specs: (_plugin,),
    )

    lookup = ProviderOptionLookup()
    resolved = lookup.resolve(
        provider="vendor_networks",
        args={"_filter": "^vpcnetwork-prod-"},
        payload={},
        field_path="infra.components[0].inputs.network_id",
    )

    assert [choice.value for choice in resolved] == ["vpcnetwork-prod-a"]
    assert lookup.last_error() is None


def test_provider_option_lookup_records_builtin_resolver_error(monkeypatch) -> None:
    lookup = ProviderOptionLookup()

    def _boom(*, args, payload, field_path):
        _ = args, payload, field_path
        raise RuntimeError("network resolver exploded")

    monkeypatch.setattr(lookup, "_resolve_project_networks", _boom)

    resolved = lookup.resolve(
        provider="project_networks",
        args={},
        payload={},
        field_path="infra.components[0].inputs.network_id",
    )

    assert resolved == []
    assert lookup.last_error() == "project_networks: network resolver exploded"


def test_provider_option_lookup_records_plugin_error(monkeypatch) -> None:
    def _plugin(**kwargs):
        _ = kwargs
        raise RuntimeError("plugin resolver exploded")

    monkeypatch.setenv(
        "NEBIUS_CXCLI_PROVIDER_OPTION_PLUGINS",
        "tests.test_provider_option_plugins:_plugin",
    )
    monkeypatch.setattr(
        "nebius_cxcli.provider_options._load_option_plugins",
        lambda _specs: (_plugin,),
    )

    lookup = ProviderOptionLookup()
    resolved = lookup.resolve(
        provider="vendor_networks",
        args={},
        payload={},
        field_path="infra.components[0].inputs.network_id",
    )

    assert resolved == []
    assert lookup.last_error() == "vendor_networks: plugin resolver exploded"


def test_provider_option_lookup_sdk_uses_shared_sdk_auth(monkeypatch) -> None:
    lookup = ProviderOptionLookup()
    captured: dict[str, object] = {}
    sdk = object()

    monkeypatch.setenv("NEBIUS_CXCLI_PROVIDER_SDK_CONFIG_FILE", "/tmp/provider-sdk-config.yaml")
    monkeypatch.setenv("NEBIUS_CXCLI_PROVIDER_AUTH_PROFILE", "dev")
    monkeypatch.setenv("NEBIUS_CXCLI_PROVIDER_AUTH_ENDPOINT", "api.example.invalid")
    monkeypatch.setattr(
        "nebius_cxcli.provider_options.init_nebius_sdk",
        lambda *, profile, endpoint, config_file, context: (
            captured.update(
                {
                    "profile": profile,
                    "endpoint": endpoint,
                    "config_file": config_file,
                    "context": context,
                }
            )
            or sdk
        ),
    )

    assert lookup._sdk_or_none() is sdk
    assert captured == {
        "profile": "dev",
        "endpoint": "api.example.invalid",
        "config_file": Path("/tmp/provider-sdk-config.yaml"),
        "context": "provider option lookup",
    }


def test_compute_platforms_use_live_project_inventory(monkeypatch) -> None:
    _install_fake_compute_module(
        monkeypatch,
        platforms=[
            ("gpu-h100-sxm", "GPU H100 SXM"),
            ("cpu-d3", "CPU D3"),
            ("cpu-e2", None),
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="compute_platforms",
        args={"platform_prefix": "cpu-"},
        payload={"client_info": {"nebius": {"project_id": "project-123"}}},
        field_path="infra.components[0].inputs.cpu_nodes_platform",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("cpu-d3", "cpu-d3  (CPU D3)"),
        ("cpu-e2", "cpu-e2"),
    ]


def test_compute_public_image_families_follow_platform_and_catalog_preferences(
    monkeypatch,
) -> None:
    _install_fake_compute_module(
        monkeypatch,
        platforms=[("gpu-h100-sxm", "GPU H100 SXM"), ("cpu-d3", "CPU D3")],
        public_images=[
            {
                "image_family": "ubuntu24.04-cuda12",
                "image_family_human_readable": "Ubuntu 24.04 CUDA 12",
                "recommended_platforms": ["gpu-h100-sxm"],
            },
            {
                "image_family": "ubuntu24.04-cuda13.0",
                "image_family_human_readable": "Ubuntu 24.04 CUDA 13",
                "recommended_platforms": ["gpu-h100-sxm"],
            },
            {
                "image_family": "ubuntu24.04-driverless",
                "image_family_human_readable": "Ubuntu 24.04 Driverless",
                "recommended_platforms": ["cpu-d3"],
            },
        ],
    )

    monkeypatch.setattr(
        "nebius_cxcli.provider_options._vm_image_preference_lists",
        lambda: (
            ("ubuntu24.04-driverless",),
            ("ubuntu24.04-cuda13.0", "ubuntu24.04-cuda12"),
        ),
    )
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="compute_public_image_families",
        args={"platform_path": "infra.components[0].inputs.platform"},
        payload={
            "client_info": {"nebius": {"region_id": "eu-north1"}},
            "infra": {"components": [{"inputs": {"platform": "gpu-h100-sxm"}}]},
        },
        field_path="infra.components[0].inputs.source_image_family",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        (
            "ubuntu24.04-cuda13.0",
            "ubuntu24.04-cuda13.0  (Ubuntu 24.04 CUDA 13, recommended)",
        ),
        (
            "ubuntu24.04-cuda12",
            "ubuntu24.04-cuda12  (Ubuntu 24.04 CUDA 12, recommended)",
        ),
        (
            "ubuntu24.04-driverless",
            "ubuntu24.04-driverless  (Ubuntu 24.04 Driverless, compatible)",
        ),
    ]


def test_mk8s_compatible_platforms_intersect_project_inventory(monkeypatch) -> None:
    _install_fake_mk8s_module(
        monkeypatch,
        compatible_platforms=["cpu-d3", "gpu-h100-sxm", "gpu-h200-sxm"],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    monkeypatch.setattr(lookup, "_resolve_k8s_version", lambda payload, args, field_path="": "1.31")
    monkeypatch.setattr(
        lookup,
        "_resolve_project_compute_platform_inventory",
        lambda project_id: (
            OptionChoice(value="gpu-h100-sxm", label="gpu-h100-sxm  (GPU H100 SXM)"),
            OptionChoice(value="gpu-l40s", label="gpu-l40s  (GPU L40S)"),
        ),
    )

    resolved = lookup.resolve(
        provider="mk8s_compatible_platforms",
        args={"platform_prefix": "gpu-", "project_id": "project-123"},
        payload={},
        field_path="infra.components[0].inputs.gpu_nodes_platform",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("gpu-h100-sxm", "gpu-h100-sxm  (GPU H100 SXM)"),
    ]


def test_mk8s_compatible_platforms_fall_back_to_matrix_without_project_scope(monkeypatch) -> None:
    _install_fake_mk8s_module(
        monkeypatch,
        compatible_platforms=["cpu-d3", "gpu-h100-sxm"],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    monkeypatch.setattr(lookup, "_resolve_k8s_version", lambda payload, args, field_path="": "1.31")

    resolved = lookup.resolve(
        provider="mk8s_compatible_platforms",
        args={"platform_prefix": "cpu-"},
        payload={},
        field_path="infra.components[0].inputs.cpu_nodes_platform",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("cpu-d3", "cpu-d3"),
    ]


def test_mk8s_gpu_stack_presets_follow_selected_platform(monkeypatch) -> None:
    _install_fake_mk8s_module(
        monkeypatch,
        compatibility_items=[
            {
                "compatible_platforms": ["gpu-b200-sxm"],
                "os": "ubuntu24.04",
            },
            {
                "compatible_platforms": ["gpu-b200-sxm"],
                "drivers_preset": "cuda13.0",
                "os": "ubuntu24.04",
            },
            {
                "compatible_platforms": ["gpu-h100-sxm"],
                "drivers_preset": "cuda12.8",
                "os": "ubuntu22.04",
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    monkeypatch.setattr(lookup, "_resolve_k8s_version", lambda payload, args, field_path="": "1.33")

    resolved = lookup.resolve(
        provider="mk8s_gpu_stack_presets",
        args={"platform_path": "infra.components[0].inputs.gpu_nodes_platform"},
        payload={
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "gpu_nodes_platform": "gpu-b200-sxm",
                        }
                    }
                ]
            }
        },
        field_path="infra.components[0].inputs.gpu_stack_preset",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("cuda13.0", "cuda13.0  (ubuntu24.04)"),
    ]


def test_mk8s_node_group_os_values_follow_selected_driver_preset(monkeypatch) -> None:
    _install_fake_mk8s_module(
        monkeypatch,
        compatibility_items=[
            {
                "compatible_platforms": ["gpu-h100-sxm"],
                "drivers_preset": "cuda12.8",
                "os": "ubuntu22.04",
            },
            {
                "compatible_platforms": ["gpu-h100-sxm"],
                "drivers_preset": "cuda13.0",
                "os": "ubuntu24.04",
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    monkeypatch.setattr(lookup, "_resolve_k8s_version", lambda payload, args, field_path="": "1.33")

    resolved = lookup.resolve(
        provider="mk8s_node_group_os_values",
        args={
            "platform_path": "infra.components[0].inputs.gpu_nodes_platform",
            "stack_preset_path": "infra.components[0].inputs.gpu_stack_preset",
        },
        payload={
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "gpu_nodes_platform": "gpu-h100-sxm",
                            "gpu_stack_preset": "cuda13.0",
                        }
                    }
                ]
            }
        },
        field_path="infra.components[0].inputs.gpu_nodes_os",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("ubuntu24.04", "ubuntu24.04"),
    ]


def test_compute_platform_presets_filter_gpu_clusterable_shapes(monkeypatch) -> None:
    _install_fake_compute_module(
        monkeypatch,
        platforms=[("gpu-b200-sxm", "GPU B200 SXM")],
        presets_by_platform={
            "gpu-b200-sxm": [
                {
                    "name": "1gpu-20vcpu-224gb",
                    "vcpu_count": 20,
                    "memory_gibibytes": 224,
                    "gpu_count": 1,
                    "allow_gpu_clustering": False,
                },
                {
                    "name": "8gpu-160vcpu-1792gb",
                    "vcpu_count": 160,
                    "memory_gibibytes": 1792,
                    "gpu_count": 8,
                    "allow_gpu_clustering": True,
                },
            ]
        },
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="compute_platform_presets",
        args={
            "platform_path": "infra.components[0].inputs.gpu_nodes_platform",
            "gpu_cluster_required_path": "infra.components[0].inputs.infiniband_fabric",
        },
        payload={
            "client_info": {"nebius": {"project_id": "project-123"}},
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "gpu_nodes_platform": "gpu-b200-sxm",
                            "infiniband_fabric": "us-central1-b",
                        }
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.gpu_nodes_preset",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        (
            "8gpu-160vcpu-1792gb",
            "8gpu-160vcpu-1792gb  (vCPU=160, RAM=1792GiB, GPU=8, GPU cluster, InfiniBand)",
        ),
    ]


def test_compute_platform_presets_rank_gpu_shapes_by_live_capacity_advice(monkeypatch) -> None:
    _install_fake_compute_module(
        monkeypatch,
        platforms=[("gpu-h100-sxm", "GPU H100 SXM")],
        presets_by_platform={
            "gpu-h100-sxm": [
                {
                    "name": "1gpu-16vcpu-200gb",
                    "vcpu_count": 16,
                    "memory_gibibytes": 200,
                    "gpu_count": 1,
                    "allow_gpu_clustering": False,
                },
                {
                    "name": "8gpu-128vcpu-1600gb",
                    "vcpu_count": 128,
                    "memory_gibibytes": 1600,
                    "gpu_count": 8,
                    "allow_gpu_clustering": True,
                },
            ]
        },
    )
    _install_fake_capacity_module(
        monkeypatch,
        resource_advice_items=[
            {
                "region": "eu-north1",
                "platform": "gpu-h100-sxm",
                "preset": "1gpu-16vcpu-200gb",
                "fabric": "fabric-2",
                "on_demand_available": 0,
                "on_demand_limit": 82,
                "on_demand_level": "AVAILABILITY_LEVEL_LOW",
                "reserved_available": 0,
                "reserved_limit": 0,
                "reserved_level": "AVAILABILITY_LEVEL_LIMIT_REACHED",
            },
            {
                "region": "eu-north1",
                "platform": "gpu-h100-sxm",
                "preset": "8gpu-128vcpu-1600gb",
                "fabric": "fabric-2",
                "on_demand_available": 2,
                "on_demand_limit": 10,
                "on_demand_level": "AVAILABILITY_LEVEL_MEDIUM",
                "reserved_available": 0,
                "reserved_limit": 0,
                "reserved_level": "AVAILABILITY_LEVEL_LIMIT_REACHED",
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="compute_platform_presets",
        args={"platform_path": "infra.components[0].inputs.gpu_nodes_platform"},
        payload={
            "client_info": {
                "nebius": {
                    "tenant_id": "tenant-123",
                    "project_id": "project-123",
                    "region_id": "eu-north1",
                }
            },
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "gpu_nodes_platform": "gpu-h100-sxm",
                        }
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.gpu_nodes_preset",
    )

    assert [(choice.value, choice.label, choice.recommended) for choice in resolved] == [
        (
            "8gpu-128vcpu-1600gb",
            "8gpu-128vcpu-1600gb  (vCPU=128, RAM=1600GiB, GPU=8, GPU cluster, InfiniBand), live on-demand VMs=2, reserved VMs=0, best fabric fabric-2, recommended",
            True,
        ),
        (
            "1gpu-16vcpu-200gb",
            "1gpu-16vcpu-200gb  (vCPU=16, RAM=200GiB, GPU=1, Ethernet only, testing/dev), live on-demand VMs=0, reserved VMs=0",
            False,
        ),
    ]


def test_mk8s_boot_disk_types_labels_match_guided_contract() -> None:
    lookup = ProviderOptionLookup()

    resolved = lookup.resolve(
        provider="mk8s_boot_disk_types",
        args={},
        payload={},
        field_path="infra.components[0].inputs.cpu_nodes_boot_disk_type",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        (
            "NETWORK_SSD",
            "NETWORK_SSD  (1-8192 GiB, 450 MiB/s, 20k/40k IOPS, reliable, encryption always on)",
        ),
        (
            "NETWORK_SSD_NON_REPLICATED",
            "NETWORK_SSD_NON_REPLICATED  (93 GiB units, 1 GiB/s, 75k/75k IOPS, lowest-cost high-performance, no redundancy)",
        ),
        (
            "NETWORK_SSD_IO_M3",
            "NETWORK_SSD_IO_M3  (93 GiB units, 1 GiB/s, 75k/75k IOPS, replicated, most expensive)",
        ),
    ]


def test_mk8s_infiniband_fabrics_skip_non_clusterable_gpu_presets(monkeypatch) -> None:
    _install_fake_compute_module(
        monkeypatch,
        platforms=[("gpu-b200-sxm", "GPU B200 SXM")],
        presets_by_platform={
            "gpu-b200-sxm": [
                {
                    "name": "1gpu-20vcpu-224gb",
                    "vcpu_count": 20,
                    "memory_gibibytes": 224,
                    "gpu_count": 1,
                    "allow_gpu_clustering": False,
                },
                {
                    "name": "8gpu-160vcpu-1792gb",
                    "vcpu_count": 160,
                    "memory_gibibytes": 1792,
                    "gpu_count": 8,
                    "allow_gpu_clustering": True,
                },
            ]
        },
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="mk8s_infiniband_fabrics",
        args={
            "platform_path": "infra.components[0].inputs.gpu_nodes_platform",
            "preset_path": "infra.components[0].inputs.gpu_nodes_preset",
        },
        payload={
            "client_info": {
                "nebius": {
                    "project_id": "project-123",
                    "region_id": "us-central1",
                }
            },
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "gpu_nodes_platform": "gpu-b200-sxm",
                            "gpu_nodes_preset": "1gpu-20vcpu-224gb",
                        }
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.infiniband_fabric",
    )

    assert resolved == []


def test_mk8s_infiniband_fabrics_use_live_capacity_rows_for_clusterable_shape(
    monkeypatch,
) -> None:
    _install_fake_compute_module(
        monkeypatch,
        platforms=[("gpu-h200-sxm", "GPU H200 SXM")],
        presets_by_platform={
            "gpu-h200-sxm": [
                {
                    "name": "8gpu-128vcpu-1600gb",
                    "vcpu_count": 128,
                    "memory_gibibytes": 1600,
                    "gpu_count": 8,
                    "allow_gpu_clustering": True,
                }
            ]
        },
    )
    _install_fake_capacity_module(
        monkeypatch,
        resource_advice_items=[
            {
                "region": "us-central1",
                "platform": "gpu-h200-sxm",
                "preset": "8gpu-128vcpu-1600gb",
                "fabric": "us-central1-new-fabric",
                "on_demand_available": 1,
                "on_demand_limit": 8,
                "reserved_available": 0,
                "reserved_limit": 0,
            },
            {
                "region": "us-central1",
                "platform": "gpu-h200-sxm",
                "preset": "8gpu-128vcpu-1600gb",
                "fabric": "N/A",
                "on_demand_available": 9,
                "on_demand_limit": 9,
                "reserved_available": 0,
                "reserved_limit": 0,
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="mk8s_infiniband_fabrics",
        args={
            "platform_path": "infra.components[0].inputs.gpu_nodes_platform",
            "preset_path": "infra.components[0].inputs.gpu_nodes_preset",
        },
        payload={
            "client_info": {
                "nebius": {
                    "tenant_id": "tenant-123",
                    "project_id": "project-123",
                    "region_id": "us-central1",
                }
            },
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "gpu_nodes_platform": "gpu-h200-sxm",
                            "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
                        }
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.infiniband_fabric",
    )

    assert [(choice.value, choice.label, choice.recommended) for choice in resolved] == [
        (
            "us-central1-new-fabric",
            "us-central1-new-fabric  (gpu-h200-sxm, us-central1), live on-demand VMs=1, reserved VMs=0, recommended",
            True,
        ),
    ]


def test_mk8s_infiniband_fabrics_rank_live_capacity_and_mark_recommended(monkeypatch) -> None:
    _install_fake_compute_module(
        monkeypatch,
        platforms=[("gpu-h100-sxm", "GPU H100 SXM")],
        presets_by_platform={
            "gpu-h100-sxm": [
                {
                    "name": "8gpu-128vcpu-1600gb",
                    "vcpu_count": 128,
                    "memory_gibibytes": 1600,
                    "gpu_count": 8,
                    "allow_gpu_clustering": True,
                }
            ]
        },
    )
    _install_fake_capacity_module(
        monkeypatch,
        resource_advice_items=[
            {
                "region": "eu-north1",
                "platform": "gpu-h100-sxm",
                "preset": "8gpu-128vcpu-1600gb",
                "fabric": "fabric-4",
                "on_demand_available": 0,
                "on_demand_limit": 10,
                "on_demand_level": "AVAILABILITY_LEVEL_LOW",
                "reserved_available": 0,
                "reserved_limit": 0,
                "reserved_level": "AVAILABILITY_LEVEL_LIMIT_REACHED",
            },
            {
                "region": "eu-north1",
                "platform": "gpu-h100-sxm",
                "preset": "8gpu-128vcpu-1600gb",
                "fabric": "fabric-2",
                "on_demand_available": 2,
                "on_demand_limit": 10,
                "on_demand_level": "AVAILABILITY_LEVEL_MEDIUM",
                "reserved_available": 0,
                "reserved_limit": 0,
                "reserved_level": "AVAILABILITY_LEVEL_LIMIT_REACHED",
            },
            {
                "region": "eu-north1",
                "platform": "gpu-h100-sxm",
                "preset": "8gpu-128vcpu-1600gb",
                "fabric": "fabric-3",
                "on_demand_available": 0,
                "on_demand_limit": 10,
                "on_demand_level": "AVAILABILITY_LEVEL_LOW",
                "reserved_available": 0,
                "reserved_limit": 0,
                "reserved_level": "AVAILABILITY_LEVEL_LIMIT_REACHED",
            },
            {
                "region": "eu-north1",
                "platform": "gpu-h100-sxm",
                "preset": "8gpu-128vcpu-1600gb",
                "fabric": "fabric-9",
                "on_demand_available": 0,
                "on_demand_limit": 10,
                "on_demand_level": "AVAILABILITY_LEVEL_LOW",
                "reserved_available": 0,
                "reserved_limit": 0,
                "reserved_level": "AVAILABILITY_LEVEL_LIMIT_REACHED",
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="mk8s_infiniband_fabrics",
        args={
            "platform_path": "infra.components[0].inputs.gpu_nodes_platform",
            "preset_path": "infra.components[0].inputs.gpu_nodes_preset",
        },
        payload={
            "client_info": {
                "nebius": {
                    "tenant_id": "tenant-123",
                    "project_id": "project-123",
                    "region_id": "eu-north1",
                }
            },
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "gpu_nodes_platform": "gpu-h100-sxm",
                            "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
                        }
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.infiniband_fabric",
    )

    assert [(choice.value, choice.label, choice.recommended) for choice in resolved] == [
        (
            "fabric-2",
            "fabric-2  (gpu-h100-sxm, eu-north1), live on-demand VMs=2, reserved VMs=0, recommended",
            True,
        ),
        (
            "fabric-3",
            "fabric-3  (gpu-h100-sxm, eu-north1), live on-demand VMs=0, reserved VMs=0",
            False,
        ),
        (
            "fabric-4",
            "fabric-4  (gpu-h100-sxm, eu-north1), live on-demand VMs=0, reserved VMs=0",
            False,
        ),
        (
            "fabric-9",
            "fabric-9  (gpu-h100-sxm, eu-north1), live on-demand VMs=0, reserved VMs=0",
            False,
        ),
    ]


def test_resolve_k8s_version_prefers_dynamic_component_input_path() -> None:
    lookup = ProviderOptionLookup()
    lookup._cache[("mk8s_control_plane_versions",)] = (OptionChoice(value="1.32", label="1.32"),)

    resolved = lookup._resolve_k8s_version(
        payload={
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "k8s_version": "1.31",
                        }
                    }
                ]
            }
        },
        args={},
        field_path="infra.components[0].inputs.cpu_nodes_platform",
    )

    assert resolved == "1.31"


def test_mk8s_infiniband_fabrics_report_manual_fallback_when_live_rows_are_missing(
    monkeypatch,
) -> None:
    _install_fake_compute_module(
        monkeypatch,
        platforms=[("gpu-h200-sxm", "GPU H200 SXM")],
        presets_by_platform={
            "gpu-h200-sxm": [
                {
                    "name": "8gpu-128vcpu-1600gb",
                    "vcpu_count": 128,
                    "memory_gibibytes": 1600,
                    "gpu_count": 8,
                    "allow_gpu_clustering": True,
                }
            ]
        },
    )
    _install_fake_capacity_module(monkeypatch, resource_advice_items=[])

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="mk8s_infiniband_fabrics",
        args={
            "platform_path": "infra.components[0].inputs.gpu_nodes_platform",
            "preset_path": "infra.components[0].inputs.gpu_nodes_preset",
        },
        payload={
            "client_info": {
                "nebius": {
                    "tenant_id": "tenant-123",
                    "project_id": "project-123",
                    "region_id": "us-central1",
                }
            },
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "gpu_nodes_platform": "gpu-h200-sxm",
                            "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
                        }
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.infiniband_fabric",
    )

    assert resolved == []
    assert lookup.last_error() == (
        "Live Capacity Dashboard returned no fabric rows for the selected "
        "cluster-capable GPU shape gpu-h200-sxm/8gpu-128vcpu-1600gb in us-central1."
    )
