from __future__ import annotations

from types import SimpleNamespace

import pytest

from nebius_cxcli.mk8s_preflight import (
    has_mk8s_resource_name_preflight_targets,
    validate_mk8s_network_preflight,
    validate_mk8s_resource_name_preflight,
)


def _fake_subnet(pool_cidr: str) -> SimpleNamespace:
    return SimpleNamespace(
        spec=SimpleNamespace(
            ipv4_private_pools=SimpleNamespace(
                pools=[
                    SimpleNamespace(
                        cidrs=[SimpleNamespace(cidr=pool_cidr)],
                    )
                ]
            )
        )
    )


def test_validate_mk8s_network_preflight_rejects_single_pool_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRequest:
        def wait(self) -> SimpleNamespace:
            return _fake_subnet("10.96.0.0/16")

    class _FakeSubnetServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            return _FakeRequest()

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", lambda **_: _FakeSDK())
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
                    "inputs": {
                        "parent_id": "project-123",
                        "cluster_name": "cluster-a",
                        "subnet_id": "vpcsubnet-123",
                        "cpu_nodes_platform": "cpu-d3",
                        "cpu_nodes_preset": "4vcpu-16gb",
                        "gpu_enabled": False,
                        "kube_network_service_cidrs": ["/16"],
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    with pytest.raises(RuntimeError, match="MK8s network preflight failed"):
        validate_mk8s_network_preflight(config)


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
                    "inputs": {
                        "parent_id": "project-123",
                        "cluster_name": "cluster-a",
                        "subnet_id": "vpcsubnet-123",
                        "cpu_nodes_platform": "cpu-d3",
                        "cpu_nodes_preset": "4vcpu-16gb",
                        "gpu_enabled": True,
                        "gpu_node_groups": 1,
                        "gpu_nodes_count_per_group": 1,
                        "gpu_nodes_platform": "gpu-h100-sxm",
                        "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
                        "infiniband_fabric": "fabric-1",
                    },
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
                    "inputs": {
                        "parent_id": "project-123",
                        "cluster_name": "cluster-a",
                        "subnet_id": "vpcsubnet-123",
                        "cpu_nodes_platform": "cpu-d3",
                        "cpu_nodes_preset": "4vcpu-16gb",
                        "gpu_enabled": True,
                        "gpu_node_groups": 1,
                        "gpu_nodes_count_per_group": 1,
                        "gpu_nodes_platform": "gpu-h100-sxm",
                        "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
                        "infiniband_fabric": "fabric-1",
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_mk8s_resource_name_preflight(
        config,
        managed_gpu_cluster_names={"cluster-a-gpu-cluster"},
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
                    "inputs": {
                        "parent_id": "project-123",
                        "cluster_name": "cluster-a",
                        "subnet_id": "vpcsubnet-123",
                        "cpu_nodes_platform": "cpu-d3",
                        "cpu_nodes_preset": "4vcpu-16gb",
                        "gpu_enabled": True,
                        "gpu_node_groups": 1,
                        "gpu_nodes_count_per_group": 1,
                        "gpu_nodes_platform": "gpu-h100-sxm",
                        "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
                        "infiniband_fabric": "fabric-1",
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }

    validate_mk8s_resource_name_preflight(config)


def test_has_mk8s_resource_name_preflight_targets_detects_enabled_mk8s_component() -> None:
    assert has_mk8s_resource_name_preflight_targets(
        {
            "infra": {
                "components": [
                    {
                        "id": "mk8s",
                        "enabled": True,
                        "source": "../../platform-infra/modules/mk8s",
                        "inputs": {
                            "parent_id": "project-123",
                            "cluster_name": "cluster-a",
                        },
                    }
                ]
            }
        }
    )
