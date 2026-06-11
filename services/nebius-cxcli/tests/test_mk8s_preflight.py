from __future__ import annotations

from types import SimpleNamespace

import pytest

from nebius_cxcli.mk8s_preflight import (
    has_mk8s_gpu_stack_compatibility_preflight_targets,
    has_mk8s_resource_name_preflight_targets,
    mk8s_node_subnet_capacity_guidance,
    validate_mk8s_gpu_stack_compatibility_preflight,
    validate_mk8s_resource_name_preflight,
    validate_vpc_networking_preflight,
)


def _fake_network(*, parent_id: str = "project-123") -> SimpleNamespace:
    return SimpleNamespace(metadata=SimpleNamespace(parent_id=parent_id))


def _fake_subnet(
    pool_cidr: str | None,
    *,
    parent_id: str = "project-123",
    network_id: str = "vpcnetwork-123",
    cidrs_as_strings: bool = False,
    status_private_cidrs: list[str] | None = None,
) -> SimpleNamespace:
    if pool_cidr is None:
        pool_cidrs = []
    elif cidrs_as_strings:
        pool_cidrs = [pool_cidr]
    else:
        pool_cidrs = [SimpleNamespace(cidr=pool_cidr)]
    return SimpleNamespace(
        metadata=SimpleNamespace(parent_id=parent_id),
        spec=SimpleNamespace(
            network_id=network_id,
            ipv4_private_pools=SimpleNamespace(
                pools=[
                    SimpleNamespace(
                        cidrs=pool_cidrs,
                    )
                ]
            )
        ),
        status=SimpleNamespace(ipv4_private_cidrs=status_private_cidrs or []),
    )


def _cpu_mk8s_inputs(*, service_cidrs: list[str] | None = None) -> dict:
    cluster = {
        "parent_id": "project-123",
        "cluster_name": "cluster-a",
        "network_id": "vpcnetwork-123",
        "subnet_id": "vpcsubnet-123",
    }
    if service_cidrs is not None:
        cluster["kube_network"] = {"service_cidrs": service_cidrs}
    return {
        "cluster": cluster,
        "node_groups": {
            "cpu": {
                "node_count": 1,
                "gpu": False,
                "platform": "cpu-d3",
                "preset": "4vcpu-16gb",
            }
        },
    }


def _gpu_mk8s_inputs() -> dict:
    inputs = _cpu_mk8s_inputs()
    inputs["node_groups"]["worker"] = {
        "node_count": 1,
        "gpu": True,
        "platform": "gpu-h100-sxm",
        "preset": "8gpu-128vcpu-1600gb",
        "gpu_cluster_key": "workers",
    }
    inputs["gpu_clusters"] = {"workers": {"infiniband_fabric": "fabric-1"}}
    return inputs


def _gpu_stack_config(*, stack_preset: str, os_value: str | None = "ubuntu24.04") -> dict:
    inputs = _cpu_mk8s_inputs()
    inputs["cluster"]["k8s_version"] = "1.33"
    inputs["node_groups"]["worker"] = {
        "node_count": 1,
        "gpu": True,
        "platform": "gpu-h100-sxm",
        "preset": "1gpu-16vcpu-200gb",
        "gpu_stack_source": "nebius_image",
        "gpu_stack_preset": stack_preset,
    }
    if os_value is not None:
        inputs["node_groups"]["worker"]["os"] = os_value
    return {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": inputs,
                }
            ]
        },
        "apps": {"charts": []},
    }


def _patch_mk8s_gpu_stack_compatibility(
    monkeypatch: pytest.MonkeyPatch,
    *,
    compatibility_items: list[dict[str, object]] | None = None,
    compatibility_by_version: dict[str, list[dict[str, object]]] | None = None,
) -> list[str]:
    requested_versions: list[str] = []

    class _FakeNodeGroupServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_compatibility_matrix(self, request: object, **kwargs: object) -> SimpleNamespace:
            requested_version = request.cluster_kubernetes_version
            requested_versions.append(requested_version)
            assert kwargs["timeout"] > 0
            assert kwargs["retries"] == 0
            if compatibility_by_version is not None:
                resolved_items = compatibility_by_version[requested_version]
            else:
                assert requested_version == "1.33"
                resolved_items = compatibility_items or []
            response = SimpleNamespace(
                versions=[
                    SimpleNamespace(
                        items=[
                            SimpleNamespace(
                                compatible_platforms=list(
                                    item.get("compatible_platforms", [])
                                ),
                                drivers_preset=item.get("drivers_preset"),
                                os=item.get("os"),
                            )
                            for item in resolved_items
                        ],
                    )
                ]
            )
            return SimpleNamespace(wait=lambda: response)

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", lambda **_: _FakeSDK())
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.NodeGroupServiceClient",
        _FakeNodeGroupServiceClient,
    )
    return requested_versions


def test_validate_mk8s_gpu_stack_compatibility_preflight_rejects_invalid_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_mk8s_gpu_stack_compatibility(
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
    config = _gpu_stack_config(stack_preset="cuda13.0")

    assert has_mk8s_gpu_stack_compatibility_preflight_targets(config) is True
    with pytest.raises(RuntimeError) as exc_info:
        validate_mk8s_gpu_stack_compatibility_preflight(config)

    message = str(exc_info.value)
    assert "gpu_stack_preset 'cuda13.0'" in message
    assert "platform 'gpu-h100-sxm' and OS 'ubuntu24.04'" in message
    assert "cuda12.8" in message


def test_validate_mk8s_gpu_stack_compatibility_preflight_rejects_missing_os(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_mk8s_gpu_stack_compatibility(
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

    with pytest.raises(RuntimeError) as exc_info:
        validate_mk8s_gpu_stack_compatibility_preflight(
            _gpu_stack_config(stack_preset="cuda13.0", os_value=None)
        )

    message = str(exc_info.value)
    assert "omits os" in message
    assert "ubuntu22.04" in message


def test_validate_mk8s_gpu_stack_compatibility_preflight_accepts_valid_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_mk8s_gpu_stack_compatibility(
        monkeypatch,
        compatibility_items=[
            {
                "compatible_platforms": ["gpu-h100-sxm"],
                "drivers_preset": "cuda12.8",
                "os": "ubuntu24.04",
            },
        ],
    )

    validate_mk8s_gpu_stack_compatibility_preflight(
        _gpu_stack_config(stack_preset="cuda12.8")
    )


def test_validate_mk8s_gpu_stack_compatibility_preflight_uses_node_group_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_versions = _patch_mk8s_gpu_stack_compatibility(
        monkeypatch,
        compatibility_by_version={
            "1.31": [
                {
                    "compatible_platforms": ["gpu-h100-sxm"],
                    "drivers_preset": "cuda12.4",
                    "os": "ubuntu22.04",
                },
            ],
            "1.32": [
                {
                    "compatible_platforms": ["gpu-h100-sxm"],
                    "drivers_preset": "cuda13.0",
                    "os": "ubuntu24.04",
                },
            ],
        },
    )
    config = _gpu_stack_config(stack_preset="cuda12.4", os_value="ubuntu22.04")
    inputs = config["infra"]["components"][0]["inputs"]
    inputs["cluster"]["k8s_version"] = "1.32"
    inputs["node_groups"]["worker"]["version"] = "1.31"

    validate_mk8s_gpu_stack_compatibility_preflight(config)

    assert requested_versions == ["1.31"]


def _patch_vpc_clients(
    monkeypatch: pytest.MonkeyPatch,
    *,
    subnet_network_ids: dict[str, str] | None = None,
    subnet_pool_cidrs: dict[str, str] | None = None,
    project_id: str = "project-123",
) -> None:
    class _FakeRequest:
        def __init__(self, value: SimpleNamespace) -> None:
            self._value = value

        def wait(self) -> SimpleNamespace:
            return self._value

    class _FakeNetworkServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            _ = request
            return _FakeRequest(_fake_network(parent_id=project_id))

    class _FakeSubnetServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            subnet_id = str(getattr(request, "id", "")).strip()
            return _FakeRequest(
                _fake_subnet(
                    (subnet_pool_cidrs or {}).get(subnet_id, "10.96.0.0/12"),
                    parent_id=project_id,
                    network_id=(subnet_network_ids or {}).get(subnet_id, "vpcnetwork-123"),
                )
            )

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", lambda **_: _FakeSDK())
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.NetworkServiceClient",
        _FakeNetworkServiceClient,
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.SubnetServiceClient",
        _FakeSubnetServiceClient,
    )


def test_validate_vpc_networking_preflight_rejects_single_pool_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRequest:
        def __init__(self, value: SimpleNamespace) -> None:
            self._value = value

        def wait(self) -> SimpleNamespace:
            return self._value

    class _FakeNetworkServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            return _FakeRequest(_fake_network())

    class _FakeSubnetServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            return _FakeRequest(_fake_subnet("10.96.0.0/16"))

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", lambda **_: _FakeSDK())
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.NetworkServiceClient",
        _FakeNetworkServiceClient,
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.SubnetServiceClient",
        _FakeSubnetServiceClient,
    )

    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": _cpu_mk8s_inputs(service_cidrs=["/16"]),
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(RuntimeError, match="VPC networking preflight failed"):
        validate_vpc_networking_preflight(config)


def test_validate_vpc_networking_preflight_accepts_string_pool_cidrs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRequest:
        def __init__(self, value: SimpleNamespace) -> None:
            self._value = value

        def wait(self) -> SimpleNamespace:
            return self._value

    class _FakeNetworkServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            return _FakeRequest(_fake_network())

    class _FakeSubnetServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            return _FakeRequest(_fake_subnet("10.96.0.0/12", cidrs_as_strings=True))

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", lambda **_: _FakeSDK())
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.NetworkServiceClient",
        _FakeNetworkServiceClient,
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.SubnetServiceClient",
        _FakeSubnetServiceClient,
    )
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": _cpu_mk8s_inputs(service_cidrs=["/20"]),
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_vpc_networking_preflight(config)


def test_validate_vpc_networking_preflight_uses_status_cidr_for_prefix_allocated_subnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRequest:
        def __init__(self, value: SimpleNamespace) -> None:
            self._value = value

        def wait(self) -> SimpleNamespace:
            return self._value

    class _FakeNetworkServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            return _FakeRequest(_fake_network())

    class _FakeSubnetServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            return _FakeRequest(
                _fake_subnet("/16", status_private_cidrs=["172.21.0.0/16"])
            )

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", lambda **_: _FakeSDK())
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.NetworkServiceClient",
        _FakeNetworkServiceClient,
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.SubnetServiceClient",
        _FakeSubnetServiceClient,
    )
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": _cpu_mk8s_inputs(service_cidrs=["/20"]),
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_vpc_networking_preflight(config)


def test_validate_vpc_networking_preflight_uses_status_cidr_for_explicit_subnet_without_spec_cidr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRequest:
        def __init__(self, value: SimpleNamespace) -> None:
            self._value = value

        def wait(self) -> SimpleNamespace:
            return self._value

    class _FakeNetworkServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            return _FakeRequest(_fake_network())

    class _FakeSubnetServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            return _FakeRequest(
                _fake_subnet(None, status_private_cidrs=["172.21.0.0/16"])
            )

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", lambda **_: _FakeSDK())
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.NetworkServiceClient",
        _FakeNetworkServiceClient,
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.SubnetServiceClient",
        _FakeSubnetServiceClient,
    )
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": _cpu_mk8s_inputs(service_cidrs=["/20"]),
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_vpc_networking_preflight(config)


def test_validate_vpc_networking_preflight_rejects_malformed_pool_cidr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRequest:
        def __init__(self, value: SimpleNamespace) -> None:
            self._value = value

        def wait(self) -> SimpleNamespace:
            return self._value

    class _FakeNetworkServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            return _FakeRequest(_fake_network())

    class _FakeSubnetServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            return _FakeRequest(_fake_subnet("not-a-cidr"))

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", lambda **_: _FakeSDK())
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.NetworkServiceClient",
        _FakeNetworkServiceClient,
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.SubnetServiceClient",
        _FakeSubnetServiceClient,
    )
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": _cpu_mk8s_inputs(service_cidrs=["/20"]),
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(RuntimeError, match="malformed pool CIDR 'not-a-cidr'"):
        validate_vpc_networking_preflight(config)


def test_validate_mk8s_resource_name_preflight_rejects_existing_gpu_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRequest:
        def __init__(self, value: object) -> None:
            self._value = value

        def wait(self) -> object:
            if isinstance(self._value, Exception):
                raise self._value
            return self._value

    class _FakeClusterServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_by_name(self, request: object) -> _FakeRequest:
            _ = request
            return _FakeRequest(RuntimeError("resource not found"))

    class _FakeGpuClusterServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_by_name(self, request: object) -> _FakeRequest:
            _ = request
            return _FakeRequest(SimpleNamespace())

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", lambda **_: _FakeSDK())
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.ClusterServiceClient",
        _FakeClusterServiceClient,
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.GpuClusterServiceClient",
        _FakeGpuClusterServiceClient,
    )

    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": _gpu_mk8s_inputs(),
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(RuntimeError, match="live Nebius GPU cluster"):
        validate_mk8s_resource_name_preflight(config)


def test_validate_mk8s_resource_name_preflight_skips_state_managed_gpu_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRequest:
        def __init__(self, value: object) -> None:
            self._value = value

        def wait(self) -> object:
            if isinstance(self._value, Exception):
                raise self._value
            return self._value

    class _FakeClusterServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_by_name(self, request: object) -> _FakeRequest:
            _ = request
            return _FakeRequest(RuntimeError("resource not found"))

    class _FakeGpuClusterServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_by_name(self, request: object) -> _FakeRequest:
            _ = request
            return _FakeRequest(SimpleNamespace())

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", lambda **_: _FakeSDK())
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.ClusterServiceClient",
        _FakeClusterServiceClient,
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.GpuClusterServiceClient",
        _FakeGpuClusterServiceClient,
    )

    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": _gpu_mk8s_inputs(),
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_mk8s_resource_name_preflight(
        config,
        managed_gpu_cluster_names={"cluster-a-workers-gpu-cluster"},
    )


def test_validate_mk8s_resource_name_preflight_accepts_nebius_not_found_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeNebiusNotFoundError(RuntimeError):
        def __init__(self, message: str) -> None:
            self.status = SimpleNamespace(code=SimpleNamespace(name="NOT_FOUND"))
            super().__init__(message)

    class _FakeRequest:
        def __init__(self, value: object) -> None:
            self._value = value

        def wait(self) -> object:
            if isinstance(self._value, Exception):
                raise self._value
            return self._value

    class _FakeClusterServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_by_name(self, request: object) -> _FakeRequest:
            _ = request
            return _FakeRequest(
                _FakeNebiusNotFoundError(
                    'Request error NOT_FOUND: no mk8s cluster found with name = "cluster-a"'
                )
            )

    class _FakeGpuClusterServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_by_name(self, request: object) -> _FakeRequest:
            _ = request
            return _FakeRequest(
                _FakeNebiusNotFoundError(
                    'Request error NOT_FOUND: no gpu cluster found with name = "cluster-a-gpu-cluster"'
                )
            )

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", lambda **_: _FakeSDK())
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.ClusterServiceClient",
        _FakeClusterServiceClient,
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.GpuClusterServiceClient",
        _FakeGpuClusterServiceClient,
    )

    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": _gpu_mk8s_inputs(),
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_mk8s_resource_name_preflight(config)


def test_validate_vpc_networking_preflight_falls_back_to_client_project_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_parent_ids: list[str | None] = []

    class _FakeRequest:
        def __init__(self, value: SimpleNamespace) -> None:
            self._value = value

        def wait(self) -> SimpleNamespace:
            return self._value

    class _FakeNetworkServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            return _FakeRequest(_fake_network(parent_id="project-from-client-info"))

    class _FakeSubnetServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            _ = request
            return _FakeRequest(
                _fake_subnet(
                    "10.96.0.0/12",
                    parent_id="project-from-client-info",
                )
            )

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    def _fake_sdk(**kwargs: object) -> _FakeSDK:
        seen_parent_ids.append(kwargs.get("parent_id"))  # type: ignore[arg-type]
        return _FakeSDK()

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", _fake_sdk)
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.NetworkServiceClient",
        _FakeNetworkServiceClient,
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.SubnetServiceClient",
        _FakeSubnetServiceClient,
    )

    inputs = _cpu_mk8s_inputs(service_cidrs=["/20"])
    inputs["cluster"].pop("parent_id")
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-from-client-info",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": inputs,
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_vpc_networking_preflight(config)

    assert seen_parent_ids == ["project-from-client-info", "project-from-client-info"]


def test_validate_vpc_networking_preflight_rejects_vm_subnet_network_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_vpc_clients(
        monkeypatch,
        subnet_network_ids={"vpcsubnet-123": "vpcnetwork-other"},
    )
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "vm",
                    "enabled": True,
                    "source": "../../platform-infra/modules/vm",
                    "inputs": {
                        "parent_id": "project-123",
                        "network_id": "vpcnetwork-123",
                        "subnet_id": "vpcsubnet-123",
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(RuntimeError, match="belongs to network vpcnetwork-other"):
        validate_vpc_networking_preflight(config)


def test_validate_vpc_networking_preflight_rejects_mk8s_node_group_subnet_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_vpc_clients(
        monkeypatch,
        subnet_network_ids={
            "vpcsubnet-123": "vpcnetwork-123",
            "vpcsubnet-node": "vpcnetwork-other",
        },
    )
    inputs = _cpu_mk8s_inputs(service_cidrs=["/20"])
    inputs["node_groups"]["cpu"]["subnet_id"] = "vpcsubnet-node"
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": inputs,
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(RuntimeError, match="inputs.node_groups.cpu.subnet_id"):
        validate_vpc_networking_preflight(config)


def test_validate_vpc_networking_preflight_requires_explicit_network_interface_subnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_vpc_clients(monkeypatch)
    inputs = _cpu_mk8s_inputs(service_cidrs=["/20"])
    inputs["node_groups"]["cpu"]["network_interfaces"] = [{}]
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": inputs,
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(RuntimeError, match=r"network_interfaces\[0\]\.subnet_id is required"):
        validate_vpc_networking_preflight(config)


def test_validate_vpc_networking_preflight_rejects_live_network_planned_subnet_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_vpc_clients(monkeypatch)
    inputs = _cpu_mk8s_inputs(service_cidrs=["/20"])
    inputs["cluster"].pop("subnet_id")
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": inputs,
                    "bindings": {
                        "inputs.cluster.subnet_id": {
                            "source_component": "vpc",
                            "source_instance": "cluster1-vpc",
                            "source_output": "subnets",
                            "key": "worker",
                            "attribute": "id",
                        }
                    },
                },
                {
                    "id": "vpc",
                    "instance_id": "cluster1-vpc",
                    "enabled": True,
                    "source": "../../platform-infra/modules/vpc",
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {"existing_id": "vpcnetwork-other"},
                        "subnets": {"worker": {"name": "worker"}},
                    },
                },
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(RuntimeError, match="not selected network vpcnetwork-123"):
        validate_vpc_networking_preflight(config)


def test_validate_vpc_networking_preflight_rejects_subnet_without_node_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_vpc_clients(
        monkeypatch,
        subnet_pool_cidrs={"vpcsubnet-123": "10.0.0.0/16"},
    )
    inputs = _cpu_mk8s_inputs(service_cidrs=["/20"])
    inputs["node_groups"]["cpu"]["node_count"] = 1000
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": inputs,
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(RuntimeError) as exc_info:
        validate_vpc_networking_preflight(config)

    message = str(exc_info.value)
    assert "request 1000 node(s)" in message
    assert "need 1001 /24 Pod allocation block(s)" in message
    assert "10.0.0.0/16 provide 256" in message
    assert "at least a /14" in message
    assert "service_cidrs allocates Kubernetes Service ClusterIP space" in message


def test_validate_vpc_networking_preflight_uses_autoscaling_max_for_node_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_vpc_clients(
        monkeypatch,
        subnet_pool_cidrs={"vpcsubnet-123": "10.0.0.0/16"},
    )
    inputs = _cpu_mk8s_inputs(service_cidrs=["/20"])
    inputs["node_groups"]["cpu"].pop("node_count")
    inputs["node_groups"]["cpu"]["autoscaling"] = {
        "enabled": True,
        "min_node_count": 1,
        "max_node_count": 256,
    }
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": inputs,
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(RuntimeError) as exc_info:
        validate_vpc_networking_preflight(config)

    message = str(exc_info.value)
    assert "cpu=256 max" in message
    assert "need 257 /24 Pod allocation block(s)" in message
    assert "at least a /15" in message


def test_validate_vpc_networking_preflight_uses_planned_subnet_cidr_for_node_capacity() -> None:
    inputs = _cpu_mk8s_inputs(service_cidrs=["/28"])
    inputs["node_groups"]["cpu"]["node_count"] = 1
    inputs["cluster"].pop("network_id")
    inputs["cluster"].pop("subnet_id")
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": inputs,
                    "bindings": {
                        "inputs.cluster.network_id": {
                            "source_component": "vpc",
                            "source_instance": "cluster1-vpc",
                            "source_output": "network_id",
                        },
                        "inputs.cluster.subnet_id": {
                            "source_component": "vpc",
                            "source_instance": "cluster1-vpc",
                            "source_output": "subnets",
                            "key": "worker",
                            "attribute": "id",
                        },
                    },
                },
                {
                    "id": "vpc",
                    "instance_id": "cluster1-vpc",
                    "enabled": True,
                    "source": "../../platform-infra/modules/vpc",
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {
                            "name": "cluster1-network",
                            "ipv4_private_cidrs": ["10.10.0.0/16"],
                        },
                        "subnets": {
                            "worker": {
                                "name": "worker",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["10.10.0.0/24"],
                            }
                        },
                    },
                },
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(RuntimeError) as exc_info:
        validate_vpc_networking_preflight(config)

    message = str(exc_info.value)
    assert "planned VPC subnet" in message
    assert "need 2 /24 Pod allocation block(s)" in message
    assert "10.10.0.0/24 provide 1" in message


def test_validate_vpc_networking_preflight_uses_planned_node_group_subnet_binding_capacity() -> None:
    inputs = _cpu_mk8s_inputs(service_cidrs=["/28"])
    inputs["node_groups"]["cpu"]["node_count"] = 1
    inputs["cluster"].pop("network_id")
    inputs["cluster"].pop("subnet_id")
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": inputs,
                    "bindings": {
                        "inputs.cluster.network_id": {
                            "source_component": "vpc",
                            "source_instance": "cluster1-vpc",
                            "source_output": "network_id",
                        },
                        "inputs.cluster.subnet_id": {
                            "source_component": "vpc",
                            "source_instance": "cluster1-vpc",
                            "source_output": "subnets",
                            "key": "control",
                            "attribute": "id",
                        },
                        "inputs.node_groups.cpu.subnet_id": {
                            "source_component": "vpc",
                            "source_instance": "cluster1-vpc",
                            "source_output": "subnets",
                            "key": "worker",
                            "attribute": "id",
                        },
                    },
                },
                {
                    "id": "vpc",
                    "instance_id": "cluster1-vpc",
                    "enabled": True,
                    "source": "../../platform-infra/modules/vpc",
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {
                            "name": "cluster1-network",
                            "ipv4_private_cidrs": ["10.20.0.0/16"],
                        },
                        "subnets": {
                            "control": {
                                "name": "control",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["10.20.0.0/16"],
                            },
                            "worker": {
                                "name": "worker",
                                "use_network_private_pools": False,
                                "ipv4_private_cidrs": ["10.20.0.0/24"],
                            },
                        },
                    },
                },
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(RuntimeError) as exc_info:
        validate_vpc_networking_preflight(config)

    message = str(exc_info.value)
    assert "vpc@cluster1-vpc.subnets.worker.id" in message
    assert "need 2 /24 Pod allocation block(s)" in message
    assert "10.20.0.0/24 provide 1" in message


def test_validate_vpc_networking_preflight_rejects_planned_node_group_subnet_mismatch() -> None:
    inputs = _cpu_mk8s_inputs(service_cidrs=["/20"])
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": inputs,
                    "bindings": {
                        "inputs.node_groups.cpu.subnet_id": {
                            "source_component": "vpc",
                            "source_instance": "worker-vpc",
                            "source_output": "subnets",
                            "key": "worker",
                            "attribute": "id",
                        },
                    },
                },
                {
                    "id": "vpc",
                    "instance_id": "worker-vpc",
                    "enabled": True,
                    "source": "../../platform-infra/modules/vpc",
                    "inputs": {
                        "parent_id": "project-123",
                        "network": {"existing_id": "vpcnetwork-other"},
                        "subnets": {"worker": {"name": "worker"}},
                    },
                },
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(RuntimeError) as exc_info:
        validate_vpc_networking_preflight(config)

    message = str(exc_info.value)
    assert "inputs.node_groups.cpu.subnet_id" in message
    assert "not selected network vpcnetwork-123" in message


def test_mk8s_node_subnet_capacity_guidance_reports_needed_prefix() -> None:
    message = mk8s_node_subnet_capacity_guidance(
        node_count=1000,
        subnet_cidrs=("10.0.0.0/16",),
    )

    assert message is not None
    assert "1000 node(s) need 1001 /24 Pod allocation block(s)" in message
    assert "10.0.0.0/16 provide 256" in message
    assert "at least a /14" in message
    assert "service_cidrs are separate Service ClusterIP space" in message


def test_validate_mk8s_resource_name_preflight_checks_only_referenced_gpu_clusters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked_gpu_names: list[str] = []

    class _FakeRequest:
        def __init__(self, value: object) -> None:
            self._value = value

        def wait(self) -> object:
            if isinstance(self._value, Exception):
                raise self._value
            return self._value

    class _FakeClusterServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_by_name(self, request: object) -> _FakeRequest:
            _ = request
            return _FakeRequest(RuntimeError("resource not found"))

    class _FakeGpuClusterServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_by_name(self, request: object) -> _FakeRequest:
            checked_gpu_names.append(str(getattr(request, "name", "")))
            return _FakeRequest(RuntimeError("resource not found"))

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", lambda **_: _FakeSDK())
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.ClusterServiceClient",
        _FakeClusterServiceClient,
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.GpuClusterServiceClient",
        _FakeGpuClusterServiceClient,
    )

    inputs = _gpu_mk8s_inputs()
    inputs["gpu_clusters"]["orphan"] = {
        "name": "cluster-a-orphan-gpu-cluster",
        "infiniband_fabric": "fabric-2",
    }
    config = {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-123",
                "region_id": "us-central1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": inputs,
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_mk8s_resource_name_preflight(config)

    assert checked_gpu_names == ["cluster-a-workers-gpu-cluster"]


def test_validate_mk8s_resource_name_preflight_checks_referenced_gpu_cluster_without_fabric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked_gpu_names: list[str] = []

    class _FakeRequest:
        def __init__(self, value: object) -> None:
            self._value = value

        def wait(self) -> object:
            if isinstance(self._value, Exception):
                raise self._value
            return self._value

    class _FakeClusterServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_by_name(self, request: object) -> _FakeRequest:
            _ = request
            return _FakeRequest(RuntimeError("resource not found"))

    class _FakeGpuClusterServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_by_name(self, request: object) -> _FakeRequest:
            checked_gpu_names.append(str(getattr(request, "name", "")))
            return _FakeRequest(RuntimeError("resource not found"))

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", lambda **_: _FakeSDK())
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.ClusterServiceClient",
        _FakeClusterServiceClient,
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_preflight.GpuClusterServiceClient",
        _FakeGpuClusterServiceClient,
    )

    inputs = _gpu_mk8s_inputs()
    inputs["gpu_clusters"]["workers"].pop("infiniband_fabric")
    config = {
        "version": "v1",
        "client_info": {
            "nebius": {
                "project_id": "project-123",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": inputs,
                }
            ]
        },
    }

    validate_mk8s_resource_name_preflight(config)

    assert checked_gpu_names == ["cluster-a-workers-gpu-cluster"]


def test_has_mk8s_resource_name_preflight_targets_ignores_non_mapping_client_info() -> None:
    inputs = _cpu_mk8s_inputs()
    inputs["cluster"]["parent_id"] = ""

    assert (
        has_mk8s_resource_name_preflight_targets(
            {
                "client_info": "not-a-mapping",
                "infra": {
                    "components": [
                        {
                            "id": "mk8s",
                            "enabled": True,
                            "source": "../../platform-infra/modules/mk8s",
                            "inputs": inputs,
                        }
                    ]
                },
            }
        )
        is False
    )


def test_has_mk8s_resource_name_preflight_targets_detects_enabled_mk8s_component() -> None:
    assert has_mk8s_resource_name_preflight_targets(
        {
            "infra": {
                "components": [
                    {
                        "id": "mk8s",
                        "enabled": True,
                        "source": "../../platform-infra/modules/mk8s",
                        "inputs": _cpu_mk8s_inputs(),
                    }
                ]
            }
        }
    )
