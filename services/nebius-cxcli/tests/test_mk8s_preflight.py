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


def _cpu_mk8s_inputs(*, service_cidrs: list[str] | None = None) -> dict:
    cluster = {
        "parent_id": "project-123",
        "cluster_name": "cluster-a",
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
                    "inputs": _cpu_mk8s_inputs(service_cidrs=["/16"]),
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


def test_validate_mk8s_network_preflight_falls_back_to_client_project_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_parent_ids: list[str | None] = []

    class _FakeRequest:
        def wait(self) -> SimpleNamespace:
            return _fake_subnet("10.96.0.0/12")

    class _FakeSubnetServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get(self, request: object) -> _FakeRequest:
            _ = request
            return _FakeRequest()

    class _FakeSDK:
        def sync_close(self) -> None:
            return

    def _fake_sdk(**kwargs: object) -> _FakeSDK:
        seen_parent_ids.append(kwargs.get("parent_id"))  # type: ignore[arg-type]
        return _FakeSDK()

    monkeypatch.setattr("nebius_cxcli.mk8s_preflight.init_nebius_sdk", _fake_sdk)
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

    validate_mk8s_network_preflight(config)

    assert seen_parent_ids == ["project-from-client-info"]


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
