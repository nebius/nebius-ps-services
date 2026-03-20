from __future__ import annotations

from types import SimpleNamespace

import pytest

from nebius_cxcli.mk8s_preflight import validate_mk8s_network_preflight


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
