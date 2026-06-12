from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import nebius_cxcli.provider_options as provider_options
from nebius_cxcli.provider_options import OptionChoice, ProviderOptionLookup


def test_provider_request_kwargs_are_bounded_and_env_tunable(monkeypatch) -> None:
    monkeypatch.delenv("NEBIUS_CXCLI_PROVIDER_REQUEST_TIMEOUT_SECONDS", raising=False)
    assert provider_options._provider_request_kwargs() == {
        "timeout": 15.0,
        "per_retry_timeout": 15.0,
        "auth_timeout": 15.0,
        "retries": 0,
    }

    monkeypatch.setenv("NEBIUS_CXCLI_PROVIDER_REQUEST_TIMEOUT_SECONDS", "3.5")
    assert provider_options._provider_request_kwargs() == {
        "timeout": 3.5,
        "per_retry_timeout": 3.5,
        "auth_timeout": 3.5,
        "retries": 0,
    }


def _install_module(monkeypatch, name: str, module: ModuleType) -> None:
    parts = name.split(".")
    for index in range(1, len(parts)):
        package_name = ".".join(parts[:index])
        package = sys.modules.get(package_name)
        if package is None:
            package = cast(Any, ModuleType(package_name))
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
    compatibility_items: list[dict[str, Any]] | None = None,
    compatibility_items_at_top_level: bool = False,
    clusters: list[dict[str, Any]] | None = None,
) -> None:
    mk8s_module = cast(Any, ModuleType("nebius.api.nebius.mk8s.v1"))

    class GetNodeGroupCompatibilityMatrixRequest:
        def __init__(self, *, cluster_kubernetes_version: str) -> None:
            self.cluster_kubernetes_version = cluster_kubernetes_version

    class NodeGroupServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_compatibility_matrix(self, request: Any, **_kwargs: object) -> SimpleNamespace:
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
            response = (
                SimpleNamespace(items=items)
                if compatibility_items_at_top_level
                else SimpleNamespace(
                    versions=[
                        SimpleNamespace(
                            items=items,
                        )
                    ]
                )
            )
            return SimpleNamespace(wait=lambda: response)

    class ListClustersRequest:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    class ClusterServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def list(self, request: Any, **_kwargs: object) -> SimpleNamespace:
            assert request.parent_id == "project-123"
            assert 1 <= request.page_size <= 999
            response = SimpleNamespace(
                items=[
                    SimpleNamespace(
                        metadata=SimpleNamespace(
                            id=item.get("id"),
                            name=item.get("name"),
                        )
                    )
                    for item in (clusters or [])
                ],
                next_page_token="",
            )
            return SimpleNamespace(wait=lambda: response)

    mk8s_module.GetNodeGroupCompatibilityMatrixRequest = GetNodeGroupCompatibilityMatrixRequest
    mk8s_module.NodeGroupServiceClient = NodeGroupServiceClient
    mk8s_module.ListClustersRequest = ListClustersRequest
    mk8s_module.ClusterServiceClient = ClusterServiceClient
    _install_module(monkeypatch, "nebius.api.nebius.mk8s.v1", mk8s_module)


def _install_fake_compute_module(
    monkeypatch,
    *,
    platforms: list[tuple[str, str | None]],
    presets_by_platform: dict[str, list[dict[str, Any]]] | None = None,
    public_images: list[dict[str, Any]] | None = None,
) -> None:
    common_module = cast(Any, ModuleType("nebius.api.nebius.common.v1"))

    class GetByNameRequest:
        def __init__(self, *, parent_id: str, name: str) -> None:
            self.parent_id = parent_id
            self.name = name

    common_module.GetByNameRequest = GetByNameRequest
    _install_module(monkeypatch, "nebius.api.nebius.common.v1", common_module)

    compute_module = cast(Any, ModuleType("nebius.api.nebius.compute.v1"))

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

        def list(self, request: Any, **_kwargs: object) -> SimpleNamespace:
            assert 1 <= request.page_size <= 999
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

        def get_by_name(self, request: Any, **_kwargs: object) -> SimpleNamespace:
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

        def list_public(self, request: Any, **_kwargs: object) -> SimpleNamespace:
            assert 1 <= request.page_size <= 999
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


def _install_fake_vpc_module(
    monkeypatch,
    *,
    subnets: list[dict[str, Any]],
    networks: list[dict[str, Any]] | None = None,
    pools: list[dict[str, Any]] | None = None,
    allocations: list[dict[str, Any]] | None = None,
) -> None:
    vpc_module = cast(Any, ModuleType("nebius.api.nebius.vpc.v1"))

    class ListNetworksRequest:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    class NetworkServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def list(self, request: Any, **_kwargs: object) -> SimpleNamespace:
            assert 1 <= request.page_size <= 999
            response = SimpleNamespace(
                items=[
                    SimpleNamespace(
                        metadata=SimpleNamespace(
                            id=item.get("id"),
                            name=item.get("name"),
                        ),
                        spec=SimpleNamespace(
                            ipv4_private_pools=SimpleNamespace(
                                pools=[
                                    SimpleNamespace(pool_id=pool_id)
                                    for pool_id in list(item.get("private_pool_ids", []))
                                ]
                            )
                        ),
                    )
                    for item in (networks or [])
                ],
                next_page_token="",
            )
            return SimpleNamespace(wait=lambda: response)

    class ListSubnetsRequest:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    class SubnetServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def list(self, request: Any, **_kwargs: object) -> SimpleNamespace:
            assert 1 <= request.page_size <= 999
            response = SimpleNamespace(
                items=[
                    SimpleNamespace(
                        metadata=SimpleNamespace(
                            id=item.get("id"),
                            name=item.get("name"),
                        ),
                        spec=SimpleNamespace(
                            network_id=item.get("network_id"),
                            ipv4_private_pools=(
                                None
                                if item.get("private_pools_omitted")
                                else SimpleNamespace(
                                    use_network_pools=bool(item.get("use_network_pools", False)),
                                    pools=[
                                        SimpleNamespace(
                                            cidrs=[
                                                SimpleNamespace(cidr=cidr)
                                                for cidr in list(
                                                    item.get(
                                                        "explicit_private_cidrs",
                                                        item.get("ipv4_private_cidrs", []),
                                                    )
                                                )
                                            ]
                                        )
                                    ],
                                )
                            ),
                        ),
                        status=SimpleNamespace(
                            ipv4_private_cidrs=list(item.get("ipv4_private_cidrs", []))
                        ),
                    )
                    for item in subnets
                ],
                next_page_token="",
            )
            return SimpleNamespace(wait=lambda: response)

    class ListPoolsRequest:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    class ListAllocationsRequest:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    class AllocationServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def list(self, request: Any, **_kwargs: object) -> SimpleNamespace:
            assert 1 <= request.page_size <= 999
            response = SimpleNamespace(
                items=[
                    SimpleNamespace(
                        metadata=SimpleNamespace(
                            id=item.get("id"),
                            name=item.get("name"),
                        ),
                        spec=SimpleNamespace(
                            ipv4_private=(
                                None
                                if item.get("public")
                                else SimpleNamespace(
                                    cidr=item.get("private_cidr"),
                                    subnet_id=item.get("subnet_id", ""),
                                    pool_id=item.get("pool_id", ""),
                                )
                            )
                        ),
                        status=SimpleNamespace(
                            details=(
                                None
                                if item.get("public")
                                else SimpleNamespace(
                                    allocated_cidr=item.get(
                                        "allocated_cidr", item.get("private_cidr")
                                    ),
                                    subnet_id=item.get("subnet_id", ""),
                                    pool_id=item.get("pool_id", ""),
                                    version=item.get("version", "IPV4"),
                                )
                            )
                        ),
                    )
                    for item in (allocations or [])
                ],
                next_page_token="",
            )
            return SimpleNamespace(wait=lambda: response)

    class GetPoolRequest:
        def __init__(self, *, id: str) -> None:
            self.id = id

    class PoolServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def list(self, request: Any, **_kwargs: object) -> SimpleNamespace:
            assert 1 <= request.page_size <= 999
            response = SimpleNamespace(
                items=[
                    SimpleNamespace(
                        metadata=SimpleNamespace(
                            id=item.get("id"),
                            name=item.get("name"),
                        ),
                        spec=SimpleNamespace(
                            version=item.get("version", "IPV4"),
                            visibility=item.get("visibility", "PRIVATE"),
                            source_pool_id=item.get("source_pool_id", ""),
                            cidrs=(
                                list(item.get("cidrs", []))
                                if item.get("cidrs_as_strings")
                                else [
                                    SimpleNamespace(cidr=cidr)
                                    for cidr in list(item.get("cidrs", []))
                                ]
                            ),
                        ),
                        status=SimpleNamespace(
                            assignment=SimpleNamespace(
                                networks=list(item.get("assigned_networks", [])),
                                network_ids=list(item.get("assigned_network_ids", [])),
                                subnets=list(item.get("assigned_subnets", [])),
                                subnet_ids=list(item.get("assigned_subnet_ids", [])),
                            ),
                            cidrs=(
                                list(item.get("status_cidrs", []))
                                if item.get("status_cidrs_as_strings")
                                else [
                                    SimpleNamespace(cidr=cidr)
                                    for cidr in list(item.get("status_cidrs", []))
                                ]
                            ),
                        ),
                    )
                    for item in (pools or [])
                ],
                next_page_token="",
            )
            return SimpleNamespace(wait=lambda: response)

        def get(self, request: Any, **_kwargs: object) -> SimpleNamespace:
            pool = next((item for item in (pools or []) if item.get("id") == request.id), None)
            if pool is None:
                raise RuntimeError(f"pool not found: {request.id}")
            response = SimpleNamespace(
                metadata=SimpleNamespace(
                    id=pool.get("id"),
                    name=pool.get("name"),
                ),
                spec=SimpleNamespace(
                    cidrs=(
                        list(pool.get("cidrs", []))
                        if pool.get("cidrs_as_strings")
                        else [SimpleNamespace(cidr=cidr) for cidr in list(pool.get("cidrs", []))]
                    ),
                ),
                status=SimpleNamespace(
                    cidrs=(
                        list(pool.get("status_cidrs", []))
                        if pool.get("status_cidrs_as_strings")
                        else [
                            SimpleNamespace(cidr=cidr)
                            for cidr in list(pool.get("status_cidrs", []))
                        ]
                    )
                ),
            )
            return SimpleNamespace(wait=lambda: response)

    vpc_module.ListNetworksRequest = ListNetworksRequest
    vpc_module.ListSubnetsRequest = ListSubnetsRequest
    vpc_module.ListPoolsRequest = ListPoolsRequest
    vpc_module.ListAllocationsRequest = ListAllocationsRequest
    vpc_module.GetPoolRequest = GetPoolRequest
    vpc_module.NetworkServiceClient = NetworkServiceClient
    vpc_module.SubnetServiceClient = SubnetServiceClient
    vpc_module.PoolServiceClient = PoolServiceClient
    vpc_module.AllocationServiceClient = AllocationServiceClient
    _install_module(monkeypatch, "nebius.api.nebius.vpc.v1", vpc_module)


def _install_fake_filesystem_module(
    monkeypatch,
    *,
    filesystems: list[dict[str, Any]],
) -> None:
    compute_module = cast(Any, ModuleType("nebius.api.nebius.compute.v1"))

    class ListFilesystemsRequest:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    class FilesystemServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def list(self, request: Any, **_kwargs: object) -> SimpleNamespace:
            assert request.parent_id == "project-123"
            assert 1 <= request.page_size <= 999
            response = SimpleNamespace(
                items=[
                    SimpleNamespace(
                        metadata=SimpleNamespace(
                            id=item.get("id"),
                            name=item.get("name"),
                        ),
                        spec=SimpleNamespace(mount_tag=item.get("mount_tag")),
                        status=SimpleNamespace(),
                    )
                    for item in filesystems
                ],
                next_page_token="",
            )
            return SimpleNamespace(wait=lambda: response)

    compute_module.ListFilesystemsRequest = ListFilesystemsRequest
    compute_module.FilesystemServiceClient = FilesystemServiceClient
    _install_module(monkeypatch, "nebius.api.nebius.compute.v1", compute_module)


def _install_fake_capacity_module(
    monkeypatch,
    *,
    resource_advice_items: list[dict[str, Any]],
    capacity_block_group_items: list[dict[str, Any]] | None = None,
) -> None:
    capacity_module = cast(Any, ModuleType("nebius.api.nebius.capacity.v1"))

    class ListResourceAdviceRequest:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    class ResourceAdviceServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def list(self, request: Any, **_kwargs: object) -> SimpleNamespace:
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

    class ListCapacityBlockGroupsRequest:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    class CapacityBlockGroupStatus:
        class State:
            __members__ = {
                "STATE_ACTIVE": 1,
                "STATE_INACTIVE": 2,
                "STATE_SHUTTING": 3,
            }

        class UsageState:
            __members__ = {
                "USAGE_STATE_AVAILABLE": 1,
                "USAGE_STATE_IN_USE": 2,
            }

    class CapacityBlockGroupServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def list(self, request: Any, **_kwargs: object) -> SimpleNamespace:
            assert request.parent_id == "tenant-123"
            assert 1 <= request.page_size <= 200
            response = SimpleNamespace(
                items=[
                    SimpleNamespace(
                        metadata=SimpleNamespace(
                            id=item.get("id"),
                            name=item.get("name"),
                        ),
                        status=SimpleNamespace(
                            region=item.get("region"),
                            service=item.get("service", ""),
                            resource_affinity=SimpleNamespace(
                                compute_v1=SimpleNamespace(
                                    platform=item.get("platform"),
                                    fabric=item.get("fabric"),
                                ),
                            ),
                            state=item.get("state", 1),
                            usage_state=item.get("usage_state", 1),
                            current_limit=item.get("current_limit", 0),
                            usage=item.get("usage", 0),
                        ),
                    )
                    for item in capacity_block_group_items or []
                ],
                next_page_token="",
            )
            return SimpleNamespace(wait=lambda: response)

    capacity_module.ListResourceAdviceRequest = ListResourceAdviceRequest
    capacity_module.ResourceAdviceServiceClient = ResourceAdviceServiceClient
    capacity_module.ListCapacityBlockGroupsRequest = ListCapacityBlockGroupsRequest
    capacity_module.CapacityBlockGroupServiceClient = CapacityBlockGroupServiceClient
    capacity_module.CapacityBlockGroupStatus = CapacityBlockGroupStatus
    _install_module(monkeypatch, "nebius.api.nebius.capacity.v1", capacity_module)


def _mk8s_gpu_defaults(
    *,
    platform: str | None = None,
    preset: str | None = None,
    stack_preset: str | None = None,
    os_value: str | None = None,
    infiniband_fabric: str | None = None,
) -> dict[str, object]:
    gpu: dict[str, object] = {}
    if platform is not None:
        gpu["platform"] = platform
    if preset is not None:
        gpu["preset"] = preset
    if stack_preset is not None:
        gpu["gpu_stack_preset"] = stack_preset
    if os_value is not None:
        gpu["os"] = os_value
    if infiniband_fabric is not None:
        gpu["infiniband_fabric"] = infiniband_fabric
    return {"node_group_defaults": {"gpu": gpu}}


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


def test_provider_option_lookup_records_plugin_load_error(monkeypatch) -> None:
    provider_options._load_option_plugins.cache_clear()
    monkeypatch.setenv(
        "NEBIUS_CXCLI_PROVIDER_OPTION_PLUGINS",
        "missing_provider_plugin:choices",
    )

    lookup = ProviderOptionLookup()
    resolved = lookup.resolve(
        provider="vendor_networks",
        args={},
        payload={},
        field_path="infra.components[0].inputs.network_id",
    )

    assert resolved == []
    assert lookup.last_error()
    assert "Provider option plugin 'missing_provider_plugin:choices' could not be loaded" in (
        lookup.last_error() or ""
    )


def test_provider_option_lookup_sdk_uses_shared_sdk_auth(monkeypatch) -> None:
    lookup = ProviderOptionLookup()
    captured: dict[str, object] = {}
    sdk = object()

    monkeypatch.setenv("NEBIUS_CXCLI_PROVIDER_SDK_CONFIG_FILE", "/tmp/provider-sdk-config.yaml")
    monkeypatch.setenv("NEBIUS_CXCLI_PROVIDER_AUTH_PROFILE", "dev")
    monkeypatch.setenv("NEBIUS_CXCLI_PROVIDER_AUTH_ENDPOINT", "api.example.invalid")
    monkeypatch.setattr(
        "nebius_cxcli.provider_options.init_nebius_sdk",
        lambda *, profile, endpoint, config_file, context, prefer_operator_auth: (
            captured.update(
                {
                    "profile": profile,
                    "endpoint": endpoint,
                    "config_file": config_file,
                    "context": context,
                    "prefer_operator_auth": prefer_operator_auth,
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
        "prefer_operator_auth": True,
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
        field_path="infra.components[0].inputs.node_group_defaults.cpu.platform",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("cpu-d3", "cpu-d3  (CPU D3)"),
        ("cpu-e2", "cpu-e2"),
    ]


def test_project_subnets_filter_by_selected_network_path(monkeypatch) -> None:
    _install_fake_vpc_module(
        monkeypatch,
        subnets=[
            {
                "id": "vpcsubnet-a",
                "name": "subnet-a",
                "network_id": "vpcnetwork-a",
                "ipv4_private_cidrs": ["10.0.0.0/24"],
            },
            {
                "id": "vpcsubnet-b",
                "name": "subnet-b",
                "network_id": "vpcnetwork-b",
                "ipv4_private_cidrs": ["10.1.0.0/24"],
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="project_subnets",
        args={"network_id_path": "infra.components[0].inputs.cluster.network_id"},
        payload={
            "client_info": {"nebius": {"project_id": "project-123"}},
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "cluster": {
                                "network_id": "vpcnetwork-b",
                            }
                        }
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.cluster.subnet_id",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("vpcsubnet-b", "vpcsubnet-b  (subnet-b) (10.1.0.0/24)"),
    ]
    assert resolved[0].metadata["private_cidrs"] == ("10.1.0.0/24",)
    assert resolved[0].metadata["use_network_private_pools"] is False


def test_project_subnets_marks_inherited_network_private_pools_non_owning(monkeypatch) -> None:
    _install_fake_vpc_module(
        monkeypatch,
        subnets=[
            {
                "id": "vpcsubnet-inherited",
                "name": "inherited",
                "network_id": "vpcnetwork-default",
                "ipv4_private_cidrs": ["10.0.0.0/13"],
                "use_network_pools": True,
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="project_subnets",
        args={"network_id": "vpcnetwork-default"},
        payload={"client_info": {"nebius": {"project_id": "project-123"}}},
        field_path="infra.components[0].inputs.subnets",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("vpcsubnet-inherited", "vpcsubnet-inherited  (inherited) (10.0.0.0/13)"),
    ]
    assert resolved[0].metadata["private_cidrs"] == ()
    assert resolved[0].metadata["use_network_private_pools"] is True


def test_project_subnets_treats_omitted_private_pool_spec_as_inherited(monkeypatch) -> None:
    _install_fake_vpc_module(
        monkeypatch,
        subnets=[
            {
                "id": "vpcsubnet-inherited",
                "name": "inherited",
                "network_id": "vpcnetwork-default",
                "ipv4_private_cidrs": ["10.0.0.0/13"],
                "private_pools_omitted": True,
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="project_subnets",
        args={"network_id": "vpcnetwork-default"},
        payload={"client_info": {"nebius": {"project_id": "project-123"}}},
        field_path="infra.components[0].inputs.subnets",
    )

    assert resolved[0].metadata["private_cidrs"] == ()
    assert resolved[0].metadata["use_network_private_pools"] is True


def test_project_subnets_uses_status_cidrs_for_explicit_subnet_when_spec_cidrs_missing(
    monkeypatch,
) -> None:
    _install_fake_vpc_module(
        monkeypatch,
        subnets=[
            {
                "id": "vpcsubnet-explicit",
                "name": "explicit",
                "network_id": "vpcnetwork-default",
                "ipv4_private_cidrs": ["10.0.0.0/16"],
                "explicit_private_cidrs": [],
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="project_subnets",
        args={"network_id": "vpcnetwork-default"},
        payload={"client_info": {"nebius": {"project_id": "project-123"}}},
        field_path="infra.components[0].inputs.subnets",
    )

    assert resolved[0].metadata["private_cidrs"] == ("10.0.0.0/16",)
    assert resolved[0].metadata["use_network_private_pools"] is False


def test_project_subnets_uses_status_cidrs_for_prefix_allocated_explicit_subnet(
    monkeypatch,
) -> None:
    _install_fake_vpc_module(
        monkeypatch,
        subnets=[
            {
                "id": "vpcsubnet-explicit-prefix",
                "name": "explicit-prefix",
                "network_id": "vpcnetwork-default",
                "ipv4_private_cidrs": ["172.21.0.0/16"],
                "explicit_private_cidrs": ["/16"],
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="project_subnets",
        args={"network_id": "vpcnetwork-default"},
        payload={"client_info": {"nebius": {"project_id": "project-123"}}},
        field_path="infra.components[0].inputs.subnets",
    )

    assert resolved[0].label == (
        "vpcsubnet-explicit-prefix  (explicit-prefix) (172.21.0.0/16)"
    )
    assert resolved[0].metadata["private_cidrs"] == ("172.21.0.0/16",)
    assert resolved[0].metadata["use_network_private_pools"] is False


def test_project_private_allocations_lists_live_private_cidrs_filtered_by_resource(
    monkeypatch,
) -> None:
    _install_fake_vpc_module(
        monkeypatch,
        subnets=[],
        allocations=[
            {
                "id": "allocation-subnet",
                "name": "vm-private-ip",
                "private_cidr": "10.0.0.42",
                "subnet_id": "vpcsubnet-inherited",
            },
            {
                "id": "allocation-pool",
                "private_cidr": "10.1.0.0/24",
                "pool_id": "vpcpool-default",
            },
            {
                "id": "allocation-other",
                "private_cidr": "10.2.0.8",
                "subnet_id": "vpcsubnet-other",
            },
            {
                "id": "allocation-public",
                "public": True,
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="project_private_allocations",
        args={
            "subnet_ids": ("vpcsubnet-inherited",),
            "pool_ids": ("vpcpool-default",),
        },
        payload={"client_info": {"nebius": {"project_id": "project-123"}}},
        field_path="infra.components[0].inputs.subnets",
    )

    assert [(choice.value, choice.metadata["private_cidrs"]) for choice in resolved] == [
        ("allocation-pool", ("10.1.0.0/24",)),
        ("allocation-subnet", ("10.0.0.42/32",)),
    ]
    assert resolved[1].metadata["subnet_id"] == "vpcsubnet-inherited"


def test_project_networks_recommend_default_network(monkeypatch) -> None:
    _install_fake_vpc_module(
        monkeypatch,
        networks=[
            {
                "id": "vpcnetwork-custom",
                "name": "workloads",
                "private_pool_ids": ["vpcpool-custom"],
            },
            {
                "id": "vpcnetwork-default",
                "name": "default-network",
                "private_pool_ids": ["vpcpool-default"],
            },
        ],
        subnets=[],
        pools=[
            {
                "id": "vpcpool-default",
                "name": "default-network-pool",
                "cidrs": ["10.0.0.0/13"],
            },
            {
                "id": "vpcpool-custom",
                "name": "workloads-pool",
                "cidrs": ["172.16.0.0/12"],
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="project_networks",
        args={},
        payload={"client_info": {"nebius": {"project_id": "project-123"}}},
        field_path="infra.components[0].inputs.network.existing_id",
    )

    assert [(choice.value, choice.label, choice.recommended) for choice in resolved] == [
        ("vpcnetwork-default", "vpcnetwork-default  (default-network)", True),
        ("vpcnetwork-custom", "vpcnetwork-custom  (workloads)", False),
    ]
    assert resolved[0].metadata["private_pool_ids"] == ("vpcpool-default",)
    assert resolved[0].metadata["private_cidrs"] == ("10.0.0.0/13",)
    assert resolved[1].metadata["private_pool_ids"] == ("vpcpool-custom",)
    assert resolved[1].metadata["private_cidrs"] == ("172.16.0.0/12",)


def test_project_networks_uses_pool_status_cidrs_when_spec_cidrs_missing(
    monkeypatch,
) -> None:
    _install_fake_vpc_module(
        monkeypatch,
        networks=[
            {
                "id": "vpcnetwork-default",
                "name": "default-network",
                "private_pool_ids": ["vpcpool-default"],
            },
        ],
        subnets=[],
        pools=[
            {
                "id": "vpcpool-default",
                "name": "default-network-pool",
                "cidrs": [],
                "status_cidrs": ["10.0.0.0/13"],
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="project_networks",
        args={},
        payload={"client_info": {"nebius": {"project_id": "project-123"}}},
        field_path="infra.components[0].inputs.network.existing_id",
    )

    assert resolved[0].metadata["private_cidrs"] == ("10.0.0.0/13",)


def test_project_private_pools_lists_unassigned_live_project_private_ipv4_pools(
    monkeypatch,
) -> None:
    _install_fake_vpc_module(
        monkeypatch,
        subnets=[],
        pools=[
            {
                "id": "vpcpool-private",
                "name": "default-network-pool",
                "cidrs": ["10.0.0.0/13", "172.16.0.0/12"],
                "cidrs_as_strings": True,
                "source_pool_id": "vpcpool-source",
                "version": SimpleNamespace(name="IPV4"),
                "visibility": SimpleNamespace(name="PRIVATE"),
            },
            {
                "id": "vpcpool-assigned-network",
                "name": "default-network-pool-assigned",
                "cidrs": ["10.0.0.0/13"],
                "assigned_networks": ["vpcnetwork-default"],
            },
            {
                "id": "vpcpool-assigned-subnet",
                "name": "subnet-pool-assigned",
                "cidrs": ["172.16.0.0/24"],
                "assigned_subnets": ["vpcsubnet-existing"],
            },
            {
                "id": "vpcpool-assigned-network-ids",
                "name": "network-ids-pool-assigned",
                "cidrs": ["192.168.0.0/24"],
                "assigned_network_ids": ["vpcnetwork-live"],
            },
            {
                "id": "vpcpool-empty",
                "name": "empty-private-pool",
            },
            {
                "id": "vpcpool-public",
                "name": "public-pool",
                "cidrs": ["203.0.113.0/24"],
                "visibility": "PUBLIC",
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="project_private_pools",
        args={},
        payload={"client_info": {"nebius": {"project_id": "project-123"}}},
        field_path="infra.components[0].inputs.network.ipv4_private_pool_ids",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        (
            "vpcpool-private",
            "vpcpool-private  (default-network-pool) (10.0.0.0/13, 172.16.0.0/12)",
        ),
    ]
    assert resolved[0].metadata["source_pool_id"] == "vpcpool-source"


def test_project_filesystems_lists_live_project_filesystems(monkeypatch) -> None:
    _install_fake_filesystem_module(
        monkeypatch,
        filesystems=[
            {
                "id": "filesystem-a",
                "name": "scratch-a",
                "mount_tag": "scratch",
            },
            {
                "id": "filesystem-b",
                "name": "jail-b",
                "mount_tag": "jail",
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="project_filesystems",
        args={},
        payload={"client_info": {"nebius": {"project_id": "project-123"}}},
        field_path="infra.components[0].inputs.filesystems.scratch.existing_id",
    )

    assert [(choice.value, choice.metadata["mount_tag"]) for choice in resolved] == [
        ("filesystem-a", "scratch"),
        ("filesystem-b", "jail"),
    ]


def test_project_mk8s_clusters_lists_live_project_clusters(monkeypatch) -> None:
    _install_fake_mk8s_module(
        monkeypatch,
        clusters=[
            {
                "id": "mk8scluster-e00alpha",
                "name": "training-cluster",
            },
            {
                "id": "mk8scluster-e00beta",
                "name": "soperator-prod",
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="project_mk8s_clusters",
        args={},
        payload={"client_info": {"nebius": {"project_id": "project-123"}}},
        field_path="deploy.targets[].cluster_id",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("mk8scluster-e00beta", "soperator-prod  (mk8scluster-e00beta)"),
        ("mk8scluster-e00alpha", "training-cluster  (mk8scluster-e00alpha)"),
    ]
    assert resolved[0].metadata["target_ref"] == "soperator-prod"


def test_compute_public_image_families_follow_live_platform_recommendations(
    monkeypatch,
) -> None:
    _install_fake_compute_module(
        monkeypatch,
        platforms=[("gpu-h100-sxm", "GPU H100 SXM"), ("cpu-d3", "CPU D3")],
        public_images=[
            {
                "image_family": "ubuntu24.04-cuda12",
                "image_family_human_readable": "Ubuntu 24.04 CUDA 12",
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

    assert [(choice.value, choice.label, choice.recommended) for choice in resolved] == [
        (
            "ubuntu24.04-cuda13.0",
            "ubuntu24.04-cuda13.0  (Ubuntu 24.04 CUDA 13, recommended)",
            True,
        ),
        (
            "ubuntu24.04-cuda12",
            "ubuntu24.04-cuda12  (Ubuntu 24.04 CUDA 12, compatible)",
            False,
        ),
        (
            "ubuntu24.04-driverless",
            "ubuntu24.04-driverless  (Ubuntu 24.04 Driverless, compatible)",
            False,
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
        field_path="infra.components[0].inputs.node_group_defaults.gpu.platform",
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
        field_path="infra.components[0].inputs.node_group_defaults.cpu.platform",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("cpu-d3", "cpu-d3"),
    ]


def test_capacity_block_groups_filter_by_region_platform_and_fabric(monkeypatch) -> None:
    _install_fake_capacity_module(
        monkeypatch,
        resource_advice_items=[],
        capacity_block_group_items=[
            {
                "id": "cbg-1",
                "name": "reserved-h100-fabric-2",
                "region": "eu-north1",
                "platform": "gpu-h100-sxm",
                "fabric": "fabric-2",
                "current_limit": 8,
                "usage": 2,
            },
            {
                "id": "cbg-2",
                "name": "reserved-h100-fabric-3",
                "region": "eu-north1",
                "platform": "gpu-h100-sxm",
                "fabric": "fabric-3",
                "current_limit": 8,
                "usage": 0,
            },
            {
                "id": "cbg-3",
                "name": "inactive-h100-fabric-2",
                "region": "eu-north1",
                "platform": "gpu-h100-sxm",
                "fabric": "fabric-2",
                "state": 2,
                "current_limit": 8,
                "usage": 0,
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="capacity_block_groups",
        args={
            "platform_path": "infra.components[0].inputs.node_groups.gpu.platform",
            "fabric_path": "infra.components[0].inputs.gpu_clusters.gpu.infiniband_fabric",
        },
        payload={
            "client_info": {
                "nebius": {
                    "tenant_id": "tenant-123",
                    "region_id": "eu-north1",
                }
            },
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "node_groups": {
                                "gpu": {
                                    "platform": "gpu-h100-sxm",
                                }
                            },
                            "gpu_clusters": {
                                "gpu": {
                                    "infiniband_fabric": "fabric-2",
                                }
                            },
                        }
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.node_groups.gpu.reservation.reservation_ids",
    )

    assert [(choice.value, choice.recommended) for choice in resolved] == [("cbg-1", True)]
    assert "reserved-h100-fabric-2" in resolved[0].label
    assert "available=6" in resolved[0].label


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
        args={"platform_path": "infra.components[0].inputs.node_group_defaults.gpu.platform"},
        payload={
            "infra": {
                "components": [
                    {
                        "inputs": _mk8s_gpu_defaults(platform="gpu-b200-sxm"),
                    }
                ]
            }
        },
        field_path="infra.components[0].inputs.node_group_defaults.gpu.gpu_stack_preset",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("cuda13.0", "cuda13.0  (ubuntu24.04)"),
    ]


def test_mk8s_gpu_stack_presets_follow_selected_os(monkeypatch) -> None:
    _install_fake_mk8s_module(
        monkeypatch,
        compatibility_items=[
            {
                "compatible_platforms": ["gpu-h100-sxm"],
                "drivers_preset": "cuda13.0",
                "os": "ubuntu22.04",
            },
            {
                "compatible_platforms": ["gpu-h100-sxm"],
                "drivers_preset": "cuda12.8",
                "os": "ubuntu24.04",
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    monkeypatch.setattr(lookup, "_resolve_k8s_version", lambda payload, args, field_path="": "1.33")

    resolved = lookup.resolve(
        provider="mk8s_gpu_stack_presets",
        args={"platform_path": "infra.components[0].inputs.node_group_defaults.gpu.platform"},
        payload={
            "infra": {
                "components": [
                    {
                        "inputs": _mk8s_gpu_defaults(
                            platform="gpu-h100-sxm",
                            os_value="ubuntu24.04",
                        ),
                    }
                ]
            }
        },
        field_path="infra.components[0].inputs.node_group_defaults.gpu.gpu_stack_preset",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("cuda12.8", "cuda12.8  (ubuntu24.04)"),
    ]


def test_mk8s_gpu_stack_presets_accept_top_level_compatibility_items(monkeypatch) -> None:
    _install_fake_mk8s_module(
        monkeypatch,
        compatibility_items=[
            {
                "compatible_platforms": ["gpu-h100-sxm"],
                "drivers_preset": "cuda12.8",
                "os": "ubuntu24.04",
            },
        ],
        compatibility_items_at_top_level=True,
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    monkeypatch.setattr(lookup, "_resolve_k8s_version", lambda payload, args, field_path="": "1.33")

    resolved = lookup.resolve(
        provider="mk8s_gpu_stack_presets",
        args={"platform_path": "infra.components[0].inputs.node_group_defaults.gpu.platform"},
        payload={
            "infra": {
                "components": [
                    {
                        "inputs": _mk8s_gpu_defaults(
                            platform="gpu-h100-sxm",
                            os_value="ubuntu24.04",
                        ),
                    }
                ]
            }
        },
        field_path="infra.components[0].inputs.node_group_defaults.gpu.gpu_stack_preset",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("cuda12.8", "cuda12.8  (ubuntu24.04)"),
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
            "platform_path": "infra.components[0].inputs.node_group_defaults.gpu.platform",
            "stack_preset_path": (
                "infra.components[0].inputs.node_group_defaults.gpu.gpu_stack_preset"
            ),
        },
        payload={
            "infra": {
                "components": [
                    {
                        "inputs": _mk8s_gpu_defaults(
                            platform="gpu-h100-sxm",
                            stack_preset="cuda13.0",
                        ),
                    }
                ]
            }
        },
        field_path="infra.components[0].inputs.node_group_defaults.gpu.os",
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
            "platform_path": "infra.components[0].inputs.node_group_defaults.gpu.platform",
            "gpu_cluster_required_path": (
                "infra.components[0].inputs.node_group_defaults.gpu.infiniband_fabric"
            ),
        },
        payload={
            "client_info": {"nebius": {"project_id": "project-123"}},
            "infra": {
                "components": [
                    {
                        "inputs": _mk8s_gpu_defaults(
                            platform="gpu-b200-sxm",
                            infiniband_fabric="us-central1-b",
                        ),
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.node_group_defaults.gpu.preset",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        (
            "8gpu-160vcpu-1792gb",
            "8gpu-160vcpu-1792gb  (vCPU=160, RAM=1792GiB, GPU=8, GPU cluster, InfiniBand)",
        ),
    ]


def test_compute_platform_presets_follow_concrete_mk8s_node_group_platform_path(
    monkeypatch,
) -> None:
    _install_fake_compute_module(
        monkeypatch,
        platforms=[
            ("gpu-h100-sxm", "GPU H100 SXM"),
            ("gpu-b200-sxm", "GPU B200 SXM"),
        ],
        presets_by_platform={
            "gpu-h100-sxm": [
                {
                    "name": "8gpu-128vcpu-1600gb",
                    "vcpu_count": 128,
                    "memory_gibibytes": 1600,
                    "gpu_count": 8,
                    "allow_gpu_clustering": True,
                },
            ],
            "gpu-b200-sxm": [
                {
                    "name": "8gpu-160vcpu-1792gb",
                    "vcpu_count": 160,
                    "memory_gibibytes": 1792,
                    "gpu_count": 8,
                    "allow_gpu_clustering": True,
                },
            ],
        },
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="compute_platform_presets",
        args={"platform_path": "infra.components[0].inputs.node_groups.gpu-nodeg2.platform"},
        payload={
            "client_info": {"nebius": {"project_id": "project-123"}},
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "node_groups": {
                                "gpu-nodeg2": {
                                    "platform": "gpu-h100-sxm",
                                }
                            }
                        }
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.node_groups.gpu-nodeg2.preset",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        (
            "8gpu-128vcpu-1600gb",
            "8gpu-128vcpu-1600gb  (vCPU=128, RAM=1600GiB, GPU=8, GPU cluster, InfiniBand)",
        ),
    ]


def test_compute_platform_presets_accept_explicit_live_platform_argument(monkeypatch) -> None:
    _install_fake_compute_module(
        monkeypatch,
        platforms=[("cpu-d3", "CPU D3")],
        presets_by_platform={
            "cpu-d3": [
                {
                    "name": "cpu-8-32",
                    "vcpu_count": 8,
                    "memory_gibibytes": 32,
                    "gpu_count": 0,
                    "allow_gpu_clustering": False,
                },
            ],
        },
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="compute_platform_presets",
        args={"platform": "cpu-d3"},
        payload={"client_info": {"nebius": {"project_id": "project-123"}}},
        field_path="upgrade.cpu_preset.to_preset",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        ("cpu-8-32", "cpu-8-32  (vCPU=8, RAM=32GiB)"),
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
        args={"platform_path": "infra.components[0].inputs.node_group_defaults.gpu.platform"},
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
                        "inputs": _mk8s_gpu_defaults(platform="gpu-h100-sxm"),
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.node_group_defaults.gpu.preset",
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


def test_compute_platform_presets_summarize_reserved_capacity_for_selected_platform(
    monkeypatch,
) -> None:
    _install_fake_compute_module(
        monkeypatch,
        platforms=[
            ("gpu-h100-sxm", "GPU H100 SXM"),
            ("gpu-h200-sxm", "GPU H200 SXM"),
        ],
        presets_by_platform={
            "gpu-h100-sxm": [
                {
                    "name": "8gpu-128vcpu-1600gb",
                    "vcpu_count": 128,
                    "memory_gibibytes": 1600,
                    "gpu_count": 8,
                    "allow_gpu_clustering": True,
                },
            ],
            "gpu-h200-sxm": [
                {
                    "name": "8gpu-128vcpu-1600gb",
                    "vcpu_count": 128,
                    "memory_gibibytes": 1600,
                    "gpu_count": 8,
                    "allow_gpu_clustering": True,
                },
            ],
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
                "on_demand_available": 4,
                "reserved_available": 0,
            },
            {
                "region": "eu-north1",
                "platform": "gpu-h100-sxm",
                "preset": "8gpu-128vcpu-1600gb",
                "fabric": "fabric-6",
                "on_demand_available": 0,
                "reserved_available": 3,
            },
            {
                "region": "eu-north1",
                "platform": "gpu-h200-sxm",
                "preset": "8gpu-128vcpu-1600gb",
                "fabric": "fabric-9",
                "on_demand_available": 99,
                "reserved_available": 99,
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="compute_platform_presets",
        args={"platform_path": "infra.components[0].inputs.node_group_defaults.gpu.platform"},
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
                        "inputs": _mk8s_gpu_defaults(platform="gpu-h100-sxm"),
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.node_group_defaults.gpu.preset",
    )

    assert [(choice.value, choice.label, choice.recommended) for choice in resolved] == [
        (
            "8gpu-128vcpu-1600gb",
            "8gpu-128vcpu-1600gb  (vCPU=128, RAM=1600GiB, GPU=8, GPU cluster, InfiniBand), live on-demand VMs=4, reserved VMs=3, best fabric fabric-4, best reserved fabric fabric-6, recommended",
            True,
        ),
    ]
    assert [choice.metadata["reserved_vms"] for choice in resolved] == [3]


def test_compute_boot_disk_types_labels_match_guided_contract() -> None:
    lookup = ProviderOptionLookup()

    resolved = lookup.resolve(
        provider="compute_boot_disk_types",
        args={},
        payload={},
        field_path="infra.components[0].inputs.node_group_defaults.cpu.boot_disk.type",
    )

    assert [(choice.value, choice.label) for choice in resolved] == [
        (
            "NETWORK_SSD",
            "NETWORK_SSD  (1-8192 GiB, 450 MiB/s, 20k/40k IOPS, erasure-coded, tolerates 2 hardware failures, encryption always on)",
        ),
        (
            "NETWORK_SSD_NON_REPLICATED",
            "NETWORK_SSD_NON_REPLICATED  (93 GiB units, 1 GiB/s, 75k/75k IOPS, lowest-cost high-performance, no redundancy, encryption only when explicitly configured)",
        ),
        (
            "NETWORK_SSD_IO_M3",
            "NETWORK_SSD_IO_M3  (93 GiB units, 1 GiB/s, 75k/75k IOPS, replicated, mirrored to 3 drives, most expensive, encryption only when explicitly configured)",
        ),
    ]


def test_operator_public_ip_cidr_provider_returns_detected_ipv4_cidr(monkeypatch) -> None:
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b"203.0.113.10\n"

    def fake_urlopen(url: str, *, timeout: int):
        assert url == "https://api.ipify.org"
        assert timeout == 5
        return _Response()

    monkeypatch.setattr(provider_options.urllib.request, "urlopen", fake_urlopen)

    lookup = ProviderOptionLookup()

    resolved = lookup.resolve(
        provider="operator_public_ip_cidr",
        args={},
        payload={},
        field_path="infra.components[0].inputs.allowed_cidrs",
    )

    assert resolved == [
        OptionChoice(
            value="203.0.113.10/32",
            label="203.0.113.10/32  (detected operator public IP)",
            recommended=True,
        )
    ]


def test_soperator_profile_choices_come_from_catalog_metadata() -> None:
    lookup = ProviderOptionLookup()

    resolved = lookup.resolve(
        provider="soperator_nodesets_profiles",
        args={},
        payload={},
        field_path="apps.charts[0].profile",
    )

    assert [(choice.value, choice.label, choice.recommended) for choice in resolved] == [
        (
            "nebius-cpu-v1",
            "CPU-only workers: worker-cpu NodeSet, cpu Slurm partition, accounting DB enabled",
            False,
        ),
        (
            "nebius-mixed-v1",
            "Mixed CPU+GPU workers: worker-cpu and worker-gpu NodeSets with cpu/gpu partitions, accounting DB enabled",
            False,
        ),
        (
            "nebius-gpu-v1",
            "Production GPU layout: system/controller/login/accounting CPU groups, GPU worker NodeSet, accounting DB enabled",
            True,
        ),
    ]


def test_soperator_partition_choices_are_selected_profile_scoped() -> None:
    lookup = ProviderOptionLookup()

    cpu_choices = lookup.resolve(
        provider="soperator_partition_profiles",
        args={"default": "shape-default"},
        payload={"apps": {"charts": [{"id": "soperator", "profile": "nebius-cpu-v1"}]}},
        field_path="apps.charts[0].values.partitionProfile",
    )
    gpu_choices = lookup.resolve(
        provider="soperator_partition_profiles",
        args={"default": "shape-default"},
        payload={"apps": {"charts": [{"id": "soperator", "profile": "nebius-gpu-v1"}]}},
        field_path="apps.charts[0].values.partitionProfile",
    )
    mixed_choices = lookup.resolve(
        provider="soperator_partition_profiles",
        args={"default": "shape-default"},
        payload={"apps": {"charts": [{"id": "soperator", "profile": "nebius-mixed-v1"}]}},
        field_path="apps.charts[0].values.partitionProfile",
    )

    assert [choice.value for choice in cpu_choices] == [
        "shape-default",
        "with-debug-long",
        "with-qos-preemption",
    ]
    assert (
        cpu_choices[0].label
        == "Baseline queues: default CPU worker queue; accounting enabled; no QoS/preemption"
    )
    assert (
        gpu_choices[0].label
        == "Baseline queues: default GPU worker queue; accounting enabled; no QoS/preemption"
    )
    assert (
        gpu_choices[1].label
        == "Add debug/long queues on the same GPU workers; accounting enabled; no QoS objects"
    )
    assert "requires SlurmDBD QOS/account objects" in gpu_choices[2].label
    assert [choice.value for choice in mixed_choices] == [
        "shape-default",
        "with-debug-long",
        "with-qos-preemption",
        "with-h100-infiniband-debug-long",
    ]
    assert mixed_choices[0].label == (
        "Baseline queues: cpu/gpu shape partitions; accounting enabled; no QoS/preemption"
    )
    assert mixed_choices[0].recommended is True
    assert "H100/InfiniBand feature partitions" in mixed_choices[3].label


def test_soperator_topology_choices_are_selected_profile_scoped() -> None:
    lookup = ProviderOptionLookup()

    resolved = lookup.resolve(
        provider="soperator_topology_profiles",
        args={"default": "disabled"},
        payload={"apps": {"charts": [{"id": "soperator", "profile": "nebius-gpu-v1"}]}},
        field_path="apps.charts[0].values.topologyProfile",
    )

    assert [(choice.value, choice.recommended) for choice in resolved] == [
        ("disabled", True),
        ("nebius-tiered-tree-v1", False),
        ("nebius-nvl-rack-v1", False),
    ]
    assert "tiered tree" in resolved[1].label
    assert "NVL rack" in resolved[2].label


def test_soperator_node_group_mapping_choices_follow_profile_role() -> None:
    lookup = ProviderOptionLookup()
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "node_groups": {
                            "cpu-a": {
                                "gpu": False,
                                "platform": "cpu-d3",
                                "preset": "4vcpu-16gb",
                            },
                            "h100": {
                                "gpu": True,
                                "platform": "gpu-h100-sxm",
                                "preset": "1gpu-16vcpu-200gb",
                            },
                        }
                    },
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "profile": "nebius-gpu-v1",
                }
            ]
        },
    }

    worker_choices = lookup.resolve(
        provider="soperator_node_groups",
        args={"role": "worker"},
        payload=payload,
        field_path="apps.charts[0].values.nodeGroupMapping.worker",
    )
    controller_choices = lookup.resolve(
        provider="soperator_node_groups",
        args={"role": "controller"},
        payload=payload,
        field_path="apps.charts[0].values.nodeGroupMapping.controller",
    )

    assert [choice.value for choice in worker_choices] == ["h100"]
    assert [choice.value for choice in controller_choices] == ["cpu-a"]


def test_soperator_node_group_mapping_choices_use_external_target_inventory() -> None:
    lookup = ProviderOptionLookup()
    payload = {
        "deploy": {
            "targets": [
                {
                    "instance_id": "cluster1",
                    "kind": "external-mk8s",
                    "ownership": "external",
                    "kube_context": "nebius-cluster1-mk8scluster-123-external",
                    "inventory": {
                        "node_groups": {
                            "cpu-a": {
                                "gpu": False,
                                "platform": "cpu-d3",
                                "preset": "4vcpu-16gb",
                            },
                            "h100": {
                                "gpu": True,
                                "platform": "gpu-h100-sxm",
                                "preset": "1gpu-16vcpu-200gb",
                            },
                        }
                    },
                }
            ]
        },
        "infra": {"components": []},
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "install_mode": "onboard-existing-cluster",
                    "profile": "nebius-gpu-v1",
                }
            ]
        },
    }

    worker_choices = lookup.resolve(
        provider="soperator_node_groups",
        args={"role": "worker"},
        payload=payload,
        field_path="apps.charts[0].values.nodeGroupMapping.worker",
    )
    controller_choices = lookup.resolve(
        provider="soperator_node_groups",
        args={"role": "controller"},
        payload=payload,
        field_path="apps.charts[0].values.nodeGroupMapping.controller",
    )

    assert [choice.value for choice in worker_choices] == ["h100"]
    assert [choice.value for choice in controller_choices] == ["cpu-a"]


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
            "platform_path": "infra.components[0].inputs.node_group_defaults.gpu.platform",
            "preset_path": "infra.components[0].inputs.node_group_defaults.gpu.preset",
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
                        "inputs": _mk8s_gpu_defaults(
                            platform="gpu-b200-sxm",
                            preset="1gpu-20vcpu-224gb",
                        ),
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.node_group_defaults.gpu.infiniband_fabric",
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
            "platform_path": "infra.components[0].inputs.node_group_defaults.gpu.platform",
            "preset_path": "infra.components[0].inputs.node_group_defaults.gpu.preset",
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
                        "inputs": _mk8s_gpu_defaults(
                            platform="gpu-h200-sxm",
                            preset="8gpu-128vcpu-1600gb",
                        ),
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.node_group_defaults.gpu.infiniband_fabric",
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
            "platform_path": "infra.components[0].inputs.node_group_defaults.gpu.platform",
            "preset_path": "infra.components[0].inputs.node_group_defaults.gpu.preset",
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
                        "inputs": _mk8s_gpu_defaults(
                            platform="gpu-h100-sxm",
                            preset="8gpu-128vcpu-1600gb",
                        ),
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.node_group_defaults.gpu.infiniband_fabric",
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


def test_mk8s_infiniband_fabrics_prefer_reserved_capacity_fabric(
    monkeypatch,
) -> None:
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
                "on_demand_available": 4,
                "on_demand_limit": 4,
                "on_demand_level": "AVAILABILITY_LEVEL_HIGH",
                "reserved_available": 0,
                "reserved_limit": 0,
                "reserved_level": "AVAILABILITY_LEVEL_LIMIT_REACHED",
            },
            {
                "region": "eu-north1",
                "platform": "gpu-h100-sxm",
                "preset": "8gpu-128vcpu-1600gb",
                "fabric": "fabric-6",
                "on_demand_available": 0,
                "on_demand_limit": 4,
                "on_demand_level": "AVAILABILITY_LEVEL_LOW",
                "reserved_available": 3,
                "reserved_limit": 3,
                "reserved_level": "AVAILABILITY_LEVEL_HIGH",
            },
        ],
    )

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())

    resolved = lookup.resolve(
        provider="mk8s_infiniband_fabrics",
        args={
            "platform_path": "infra.components[0].inputs.node_group_defaults.gpu.platform",
            "preset_path": "infra.components[0].inputs.node_group_defaults.gpu.preset",
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
                        "inputs": _mk8s_gpu_defaults(
                            platform="gpu-h100-sxm",
                            preset="8gpu-128vcpu-1600gb",
                        ),
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.node_group_defaults.gpu.infiniband_fabric",
    )

    assert [(choice.value, choice.label, choice.recommended) for choice in resolved] == [
        (
            "fabric-6",
            "fabric-6  (gpu-h100-sxm, eu-north1), live on-demand VMs=0, reserved VMs=3, recommended for reservations",
            True,
        ),
        (
            "fabric-4",
            "fabric-4  (gpu-h100-sxm, eu-north1), live on-demand VMs=4, reserved VMs=0",
            False,
        ),
    ]
    assert [choice.metadata["reserved_vms"] for choice in resolved] == [3, 0]


def test_resolve_k8s_version_prefers_dynamic_component_input_path() -> None:
    lookup = ProviderOptionLookup()
    lookup._cache[("mk8s_control_plane_versions",)] = (OptionChoice(value="1.32", label="1.32"),)

    resolved = lookup._resolve_k8s_version(
        payload={
            "infra": {
                "components": [
                    {
                        "inputs": {
                            "cluster": {
                                "k8s_version": "1.31",
                            },
                        }
                    }
                ]
            }
        },
        args={},
        field_path="infra.components[0].inputs.node_group_defaults.cpu.platform",
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
            "platform_path": "infra.components[0].inputs.node_group_defaults.gpu.platform",
            "preset_path": "infra.components[0].inputs.node_group_defaults.gpu.preset",
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
                        "inputs": _mk8s_gpu_defaults(
                            platform="gpu-h200-sxm",
                            preset="8gpu-128vcpu-1600gb",
                        ),
                    }
                ]
            },
        },
        field_path="infra.components[0].inputs.node_group_defaults.gpu.infiniband_fabric",
    )

    assert resolved == []
    assert lookup.last_error() == (
        "Live Capacity Dashboard returned no fabric rows for the selected "
        "cluster-capable GPU shape gpu-h200-sxm/8gpu-128vcpu-1600gb in us-central1."
    )
