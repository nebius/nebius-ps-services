"""Provider boundary regressions using the installed SDK's real response types."""

from io import StringIO
from types import SimpleNamespace

import pytest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.compute.v1 import (
    ListPlatformsResponse,
    Platform,
    PlatformServiceClient,
    PlatformSpec,
    Preset,
    PresetResources,
)
from nebius.api.nebius.vpc.v1 import (
    IPv4PrivateSubnetPools,
    Subnet,
    SubnetCidr,
    SubnetPool,
    SubnetServiceClient,
    SubnetSpec,
    SubnetStatus,
    SubnetStatusPool,
)
from rich.console import Console

from nebius_cxcli.mk8s_preflight import _subnet_pool_cidrs
from nebius_cxcli.provider_options import ProviderOptionLookup
from nebius_cxcli.soperator_install_progress import install_progress_scope
from nebius_cxcli.soperator_upgrade_progress import SoperatorUpgradeProgress


@pytest.mark.parametrize("mode", ["explicit", "prefix", "inherited", "omitted"])
def test_sdk_subnet_pool_status_preserves_cidrs_without_deprecation(monkeypatch, caplog, mode):
    private_pools = (
        None
        if mode == "omitted"
        else IPv4PrivateSubnetPools(
            use_network_pools=mode == "inherited",
            pools=[SubnetPool(cidrs=[SubnetCidr(cidr="/24")])] if mode == "prefix" else [],
        )
    )
    subnet = Subnet(
        metadata=ResourceMetadata(id="vpcsubnet-test"),
        spec=SubnetSpec(network_id="vpcnetwork-test", ipv4_private_pools=private_pools),
        status=SubnetStatus(
            ipv4_private_pools=[
                SubnetStatusPool(cidrs=["10.0.0.0/24"]),
                SubnetStatusPool(cidrs=["10.1.0.0/24"]),
            ]
        ),
    )
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    monkeypatch.setattr(lookup, "_paged_list", lambda **kwargs: [subnet])
    monkeypatch.setattr(SubnetServiceClient, "__init__", lambda self, sdk: None)
    choices = lookup.resolve(
        provider="project_subnets", args={"project_id": "project-test"}, payload={}, field_path=""
    )
    expected_owned = ("10.0.0.0/24", "10.1.0.0/24") if mode in {"explicit", "prefix"} else ()
    assert choices[0].metadata["private_cidrs"] == expected_owned
    assert "10.0.0.0/24, 10.1.0.0/24" in choices[0].label
    assert _subnet_pool_cidrs(subnet) == expected_owned
    assert not [record for record in caplog.records if "deprecated" in record.getMessage()]


def _platform(name, preset, *, gpu=False):
    return Platform(
        metadata=ResourceMetadata(name=name),
        spec=PlatformSpec(
            presets=[
                Preset(
                    name=preset,
                    resources=PresetResources(
                        vcpu_count=8, memory_gibibytes=32, gpu_count=int(gpu)
                    ),
                    allow_gpu_clustering=gpu,
                )
            ]
        ),
    )


def test_project_inventory_serves_selected_and_previous_shapes_without_refetch(monkeypatch):
    calls = []

    def list_platforms(self, request, **kwargs):
        calls.append((request.parent_id, request.page_token))
        assert kwargs["retries"] == 0
        if not request.page_token:
            response = ListPlatformsResponse(
                items=[_platform("cpu-d3", "8vcpu-32gb")], next_page_token="next"
            )
        else:
            response = ListPlatformsResponse(
                items=[_platform("gpu-h200-sxm", "1gpu-8vcpu-32gb", gpu=True)]
            )
        return SimpleNamespace(wait=lambda: response)

    monkeypatch.setattr(PlatformServiceClient, "__init__", lambda self, sdk: None)
    monkeypatch.setattr(PlatformServiceClient, "list", list_platforms)
    monkeypatch.setattr(
        PlatformServiceClient, "get_by_name", lambda *args, **kwargs: pytest.fail("redundant fetch")
    )
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    output = StringIO()
    progress = SoperatorUpgradeProgress(Console(file=output), prefix="Soperator install")
    with install_progress_scope(progress):
        choices = lookup.resolve(
            provider="compute_platforms",
            args={"project_id": "project-test"},
            payload={},
            field_path="",
        )
        assert [choice.value for choice in choices] == ["cpu-d3", "gpu-h200-sxm"]
        for _ in range(4):
            for platform, preset, expected in [
                ("cpu-d3", "8vcpu-32gb", (8, 32, 0)),
                ("gpu-h200-sxm", "1gpu-8vcpu-32gb", (8, 32, 1)),
                ("gpu-h100-sxm", "1gpu-8vcpu-32gb", None),
            ]:
                assert (
                    lookup.compute_platform_preset_resources(
                        project_id="project-test", platform_name=platform, preset_name=preset
                    )
                    == expected
                )
        assert (
            lookup.compute_platform_preset_allows_gpu_clustering(
                project_id="project-test",
                platform_name="gpu-h200-sxm",
                preset_name="1gpu-8vcpu-32gb",
            )
            is True
        )
    assert calls == [("project-test", ""), ("project-test", "next")]
    assert "FAILED" not in output.getvalue()
    assert len(output.getvalue().splitlines()) == 2


@pytest.mark.parametrize("failed_page", ["", "next"])
def test_failed_inventory_does_not_poison_preset_choices_or_cache_partial_pages(
    monkeypatch, failed_page
):
    calls = []
    failed = False

    def list_platforms(self, request, **kwargs):
        nonlocal failed
        calls.append(request.page_token)
        if request.page_token == failed_page and not failed:
            failed = True
            raise RuntimeError("private-provider-detail")
        response = (
            ListPlatformsResponse(items=[_platform("cpu-d3", "8vcpu-32gb")], next_page_token="next")
            if not request.page_token
            else ListPlatformsResponse(
                items=[_platform("gpu-h200-sxm", "1gpu-8vcpu-32gb", gpu=True)]
            )
        )
        return SimpleNamespace(wait=lambda: response)

    monkeypatch.setattr(PlatformServiceClient, "__init__", lambda self, sdk: None)
    monkeypatch.setattr(PlatformServiceClient, "list", list_platforms)
    monkeypatch.setattr(
        PlatformServiceClient, "get_by_name", lambda *args, **kwargs: pytest.fail("redundant fetch")
    )
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    output = StringIO()
    progress = SoperatorUpgradeProgress(Console(file=output), prefix="Soperator install")
    args = {"project_id": "project-test", "platform": "cpu-d3"}
    with install_progress_scope(progress):
        assert (
            lookup.resolve(
                provider="compute_platform_presets", args=args, payload={}, field_path=""
            )
            == []
        )
        assert lookup.last_error()
        choices = lookup.resolve(
            provider="compute_platform_presets", args=args, payload={}, field_path=""
        )
    assert [choice.value for choice in choices] == ["8vcpu-32gb"]
    assert lookup.last_error() is None
    assert calls == (["", "", "next"] if not failed_page else ["", "next", "", "next"])
    assert "FAILED" in output.getvalue()
    assert "OK" in output.getvalue()
    assert "private-provider-detail" not in output.getvalue()
