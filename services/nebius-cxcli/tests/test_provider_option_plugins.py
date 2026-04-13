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
) -> None:
    compute_module = ModuleType("nebius.api.nebius.compute.v1")

    class ListPlatformsRequest:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
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

    compute_module.ListPlatformsRequest = ListPlatformsRequest
    compute_module.PlatformServiceClient = PlatformServiceClient
    _install_module(monkeypatch, "nebius.api.nebius.compute.v1", compute_module)


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


def test_mk8s_gpu_driver_presets_follow_selected_platform(monkeypatch) -> None:
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
        provider="mk8s_gpu_driver_presets",
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
        field_path="infra.components[0].inputs.gpu_drivers_preset",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("cuda13.0", "cuda13.0  (ubuntu24.04)"),
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
            "8gpu-160vcpu-1792gb  (vCPU=160, RAM=1792GiB, GPU=8, GPU cluster)",
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


def test_mk8s_infiniband_fabrics_filter_by_selected_region_and_gpu_platform() -> None:
    lookup = ProviderOptionLookup()

    resolved = lookup.resolve(
        provider="mk8s_infiniband_fabrics",
        args={"platform_path": "infra.components[0].inputs.gpu_nodes_platform"},
        payload={
            "client_info": {
                "nebius": {
                    "region_id": "us-central1",
                }
            },
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "gpu_nodes_platform": "gpu-h200-sxm",
                        }
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.infiniband_fabric",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("us-central1-a", "us-central1-a  (gpu-h200-sxm, us-central1)"),
    ]


def test_resolve_k8s_version_prefers_dynamic_component_input_path() -> None:
    lookup = ProviderOptionLookup()
    lookup._cache[("mk8s_control_plane_versions",)] = (
        OptionChoice(value="1.32", label="1.32"),
    )

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


def test_mk8s_infiniband_fabrics_return_all_platform_matches_without_region_filter() -> None:
    lookup = ProviderOptionLookup()

    resolved = lookup.resolve(
        provider="mk8s_infiniband_fabrics",
        args={"platform_path": "infra.components[0].inputs.gpu_nodes_platform"},
        payload={
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "gpu_nodes_platform": "gpu-h200-sxm",
                        }
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.infiniband_fabric",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("fabric-5", "fabric-5  (gpu-h200-sxm, eu-west1)"),
        ("fabric-7", "fabric-7  (gpu-h200-sxm, eu-north1)"),
        ("eu-north2-a", "eu-north2-a  (gpu-h200-sxm, eu-north2)"),
        ("us-central1-a", "us-central1-a  (gpu-h200-sxm, us-central1)"),
    ]
