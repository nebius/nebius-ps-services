from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

import nebius_cxcli.quota_checks as quota_checks
from nebius_cxcli.capacity_dashboard import CapacityAdviceAvailability, CapacityResourceAdvice
from nebius_cxcli.quota_checks import (
    AggregatedQuotaRequirement,
    GpuCapacityShape,
    QuotaCheck,
    QuotaContributor,
    QuotaCoverageGap,
    QuotaRecord,
    QuotaReport,
    QuotaRequestChange,
    QuotaRequirement,
    RegionalQuotaAvailability,
    _aggregate_requirements,
    _estimate_mk8s_requirements,
    _estimate_vm_requirements,
    _evaluate_requirement,
    _NpcQuotaRequestUnavailableError,
    assess_live_quota_requirements,
    format_quota_report_lines,
    format_quota_request_manual_followup_lines,
    plan_quota_request_changes,
    request_quota_changes,
)
from nebius_cxcli.terminal_styles import WARNING_COLOR

_GIB = 1024 * 1024 * 1024


def _capacity_advice(
    *,
    region: str,
    platform: str,
    preset: str,
    fabric: str,
    on_demand_available: int = 0,
    on_demand_limit: int = 0,
    on_demand_level: str = "AVAILABILITY_LEVEL_UNKNOWN",
    on_demand_data_state: str = "DATA_STATE_FRESH",
    reserved_available: int = 0,
    reserved_limit: int = 0,
    reserved_level: str = "AVAILABILITY_LEVEL_UNKNOWN",
    reserved_data_state: str = "DATA_STATE_FRESH",
    preemptible_available: int = 0,
    preemptible_limit: int = 0,
    preemptible_level: str = "AVAILABILITY_LEVEL_UNKNOWN",
    preemptible_data_state: str = "DATA_STATE_FRESH",
) -> CapacityResourceAdvice:
    return CapacityResourceAdvice(
        region=region,
        platform=platform,
        preset=preset,
        fabric=fabric,
        on_demand=CapacityAdviceAvailability(
            available=on_demand_available,
            limit=on_demand_limit,
            availability_level=on_demand_level,
            data_state=on_demand_data_state,
        ),
        reserved=CapacityAdviceAvailability(
            available=reserved_available,
            limit=reserved_limit,
            availability_level=reserved_level,
            data_state=reserved_data_state,
        ),
        preemptible=CapacityAdviceAvailability(
            available=preemptible_available,
            limit=preemptible_limit,
            availability_level=preemptible_level,
            data_state=preemptible_data_state,
        ),
    )


def _resources(
    *,
    platform: str,
    preset: str,
    vcpu_count: int,
    memory_gibibytes: int,
    gpu_count: int,
    allow_gpu_clustering: bool = False,
):
    return type(
        "_Resources",
        (),
        {
            "platform": platform,
            "preset": preset,
            "vcpu_count": vcpu_count,
            "memory_gibibytes": memory_gibibytes,
            "gpu_count": gpu_count,
            "allow_gpu_clustering": allow_gpu_clustering,
        },
    )()


def _quota_record(
    *,
    name: str,
    region: str = "eu-north1",
    limit: int | None = 10,
    usage: int = 0,
    unit: str = "count",
    description: str = "",
) -> QuotaRecord:
    return QuotaRecord(
        name=name,
        region=region,
        limit=limit,
        usage=usage,
        service=name.split(".", 1)[0],
        description=description or name,
        unit=unit,
        state="STATE_ACTIVE",
        usage_state="USAGE_STATE_NORMAL",
        usage_percentage="0",
    )


def test_available_quota_coerces_sdk_numeric_values() -> None:
    assert quota_checks._available_quota("10", "2") == 8
    assert quota_checks._available_quota(3.0, None) == 3
    assert quota_checks._available_quota("not-a-number", 1) is None


def test_aggregate_requirements_sums_shared_quota_usage() -> None:
    aggregated = _aggregate_requirements(
        [
            QuotaRequirement(
                component_id="ssh-jumphost",
                instance_id="ssh-jumphost",
                component_label="ssh-jumphost",
                quota_name="compute.instance.count",
                region="eu-north1",
                required=1,
                reason="one VM",
            ),
            QuotaRequirement(
                component_id="managed-postgresql",
                instance_id="managed-postgresql",
                component_label="managed-postgresql",
                quota_name="compute.instance.count",
                region="eu-north1",
                required=2,
                reason="two database hosts",
            ),
        ]
    )

    assert len(aggregated) == 1
    requirement = aggregated[0]
    assert requirement.required == 3
    assert requirement.component_label == "ssh-jumphost + 1 more"
    assert [item.component_label for item in requirement.contributors] == [
        "ssh-jumphost",
        "managed-postgresql",
    ]

    check = _evaluate_requirement(
        requirement,
        tenant_quotas={
            ("compute.instance.count", "eu-north1"): QuotaRecord(
                name="compute.instance.count",
                region="eu-north1",
                limit=2,
                usage=0,
                service="compute",
                description="VM count",
                unit="",
                state="STATE_ACTIVE",
                usage_state="USAGE_STATE_NORMAL",
                usage_percentage="0",
            )
        },
        project_quotas={},
    )

    assert check.available == 2
    assert check.sufficient is False
    assert check.reason == "ssh-jumphost: one VM; managed-postgresql: two database hosts"


def test_quota_session_list_quotas_rejects_repeated_page_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ListQuotaAllowancesRequest:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    calls: list[str] = []

    class _QuotaAllowanceServiceClient:
        def __init__(self, _sdk: object) -> None:
            pass

        def list(self, request: _ListQuotaAllowancesRequest):  # type: ignore[no-untyped-def]
            calls.append(request.page_token)
            response = SimpleNamespace(items=[], next_page_token="same-token")
            return SimpleNamespace(wait=lambda: response)

    quota_module = ModuleType("nebius.api.nebius.quotas.v1")
    quota_module.ListQuotaAllowancesRequest = _ListQuotaAllowancesRequest  # type: ignore[attr-defined]
    quota_module.QuotaAllowanceServiceClient = _QuotaAllowanceServiceClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.quotas.v1", quota_module)

    session = quota_checks._QuotaSession.__new__(quota_checks._QuotaSession)
    session._sdk = object()
    session._quota_cache = {}

    with pytest.raises(RuntimeError, match="repeated pagination token"):
        session.list_quotas(parent_id="tenant-1")

    assert calls == ["", "same-token"]


def test_aggregate_requirements_keep_gpu_capacity_shapes_separate() -> None:
    aggregated = _aggregate_requirements(
        [
            QuotaRequirement(
                component_id="mk8s-a",
                instance_id="mk8s-a",
                component_label="mk8s-a",
                quota_name="compute.instance.gpu.b300",
                region="uk-south1",
                required=8,
                reason="one GPU node in fabric a",
                gpu_capacity_shape=GpuCapacityShape(
                    platform="gpu-b300-sxm",
                    preset="8gpu-192vcpu-2768gb",
                    fabric="uk-south1-a",
                    mode="regular",
                    gpu_count_per_instance=8,
                ),
            ),
            QuotaRequirement(
                component_id="mk8s-b",
                instance_id="mk8s-b",
                component_label="mk8s-b",
                quota_name="compute.instance.gpu.b300",
                region="uk-south1",
                required=8,
                reason="one GPU node in fabric b",
                gpu_capacity_shape=GpuCapacityShape(
                    platform="gpu-b300-sxm",
                    preset="8gpu-192vcpu-2768gb",
                    fabric="uk-south1-b",
                    mode="regular",
                    gpu_count_per_instance=8,
                ),
            ),
        ]
    )

    assert len(aggregated) == 2
    assert {item.gpu_capacity_shape for item in aggregated} == {
        GpuCapacityShape(
            platform="gpu-b300-sxm",
            preset="8gpu-192vcpu-2768gb",
            fabric="uk-south1-a",
            mode="regular",
            gpu_count_per_instance=8,
        ),
        GpuCapacityShape(
            platform="gpu-b300-sxm",
            preset="8gpu-192vcpu-2768gb",
            fabric="uk-south1-b",
            mode="regular",
            gpu_count_per_instance=8,
        ),
    }


def test_estimate_mk8s_requirements_add_gpu_capacity_shape_for_infiniband_nodes() -> None:
    class _Session:
        def preset_resources(self, *, project_id: str, platform: str, preset: str):
            assert project_id == "project-1"
            assert platform == "gpu-b300-sxm"
            assert preset == "8gpu-192vcpu-2768gb"
            return _resources(
                platform=platform,
                preset=preset,
                vcpu_count=192,
                memory_gibibytes=2768,
                gpu_count=8,
                allow_gpu_clustering=True,
            )

    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []

    _estimate_mk8s_requirements(
        session=cast(Any, _Session()),
        project_id="project-1",
        region="uk-south1",
        component_id="mk8s",
        instance_id="mk8s",
        inputs={
            "node_groups": {
                "worker": {
                    "node_count": 1,
                    "gpu": True,
                    "platform": "gpu-b300-sxm",
                    "preset": "8gpu-192vcpu-2768gb",
                    "gpu_cluster_key": "workers",
                    "reservation": {"policy": "AUTO"},
                }
            },
            "gpu_clusters": {"workers": {"infiniband_fabric": "uk-south1-a"}},
        },
        requirements=requirements,
        gaps=gaps,
    )

    gpu_quota_requirement = next(
        item for item in requirements if item.quota_name == "compute.instance.gpu.b300"
    )

    assert gpu_quota_requirement.gpu_capacity_shape == GpuCapacityShape(
        platform="gpu-b300-sxm",
        preset="8gpu-192vcpu-2768gb",
        fabric="uk-south1-a",
        mode="auto",
        gpu_count_per_instance=8,
    )


def test_estimate_mk8s_requirements_reports_gap_for_missing_keyed_gpu_fabric() -> None:
    class _Session:
        def preset_resources(self, *, project_id: str, platform: str, preset: str):
            assert project_id == "project-1"
            assert platform == "gpu-b300-sxm"
            assert preset == "8gpu-192vcpu-2768gb"
            return _resources(
                platform=platform,
                preset=preset,
                vcpu_count=192,
                memory_gibibytes=2768,
                gpu_count=8,
                allow_gpu_clustering=True,
            )

    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []

    _estimate_mk8s_requirements(
        session=cast(Any, _Session()),
        project_id="project-1",
        region="uk-south1",
        component_id="mk8s",
        instance_id="mk8s",
        inputs={
            "node_groups": {
                "worker": {
                    "node_count": 1,
                    "gpu": True,
                    "platform": "gpu-b300-sxm",
                    "preset": "8gpu-192vcpu-2768gb",
                    "gpu_cluster_key": "workers",
                    "reservation": {"policy": "AUTO"},
                }
            },
            "gpu_clusters": {"workers": {}},
        },
        requirements=requirements,
        gaps=gaps,
    )

    gpu_quota_requirement = next(
        item for item in requirements if item.quota_name == "compute.instance.gpu.b300"
    )

    assert gpu_quota_requirement.gpu_capacity_shape is None
    assert len(gaps) == 1
    assert (
        "inputs.gpu_clusters.workers.infiniband_fabric is missing; "
        "fabric-bound GPU capacity was not checked"
    ) in gaps[0].message


def test_estimate_mk8s_requirements_cover_boot_disk_quota_from_explicit_inputs() -> None:
    class _Session:
        def preset_resources(self, *, project_id: str, platform: str, preset: str):
            if (project_id, platform, preset) == ("project-1", "cpu-d3", "32vcpu-128gb"):
                return _resources(
                    platform=platform,
                    preset=preset,
                    vcpu_count=32,
                    memory_gibibytes=128,
                    gpu_count=0,
                )
            if (project_id, platform, preset) == (
                "project-1",
                "gpu-b300-sxm",
                "8gpu-192vcpu-2768gb",
            ):
                return _resources(
                    platform=platform,
                    preset=preset,
                    vcpu_count=192,
                    memory_gibibytes=2768,
                    gpu_count=8,
                    allow_gpu_clustering=True,
                )
            raise AssertionError((project_id, platform, preset))

    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []

    _estimate_mk8s_requirements(
        session=cast(Any, _Session()),
        project_id="project-1",
        region="uk-south1",
        component_id="mk8s",
        instance_id="mk8s",
        inputs={
            "node_groups": {
                "cpu": {
                    "node_count": 2,
                    "gpu": False,
                    "platform": "cpu-d3",
                    "preset": "32vcpu-128gb",
                    "boot_disk": {
                        "size_gibibytes": 186,
                        "type": "NETWORK_SSD",
                    },
                },
                "worker": {
                    "node_count": 1,
                    "gpu": True,
                    "platform": "gpu-b300-sxm",
                    "preset": "8gpu-192vcpu-2768gb",
                    "boot_disk": {
                        "size_gibibytes": 1023,
                        "type": "NETWORK_SSD",
                    },
                    "gpu_cluster_key": "workers",
                },
            },
            "gpu_clusters": {"workers": {"infiniband_fabric": "uk-south1-a"}},
        },
        requirements=requirements,
        gaps=gaps,
    )

    disk_requirements = [
        item for item in requirements if item.quota_name == "compute.disk.size.network-ssd"
    ]
    assert len(disk_requirements) == 2
    assert sum(item.required for item in disk_requirements) == (2 * 186 + 1023) * _GIB
    assert not any("boot-disk quota could not be fully evaluated" in gap.message for gap in gaps)


def test_estimate_mk8s_requirements_cover_boot_disk_quota_from_override_template() -> None:
    class _Session:
        def preset_resources(self, *, project_id: str, platform: str, preset: str):
            return _resources(
                platform=platform,
                preset=preset,
                vcpu_count=32,
                memory_gibibytes=128,
                gpu_count=0,
            )

    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []

    _estimate_mk8s_requirements(
        session=cast(Any, _Session()),
        project_id="project-1",
        region="uk-south1",
        component_id="mk8s",
        instance_id="mk8s",
        inputs={
            "node_groups": {
                "cpu": {
                    "node_count": 1,
                    "gpu": False,
                    "platform": "cpu-d3",
                    "preset": "32vcpu-128gb",
                    "boot_disk": {
                        "size_gibibytes": 93,
                        "type": "NETWORK_SSD",
                    },
                }
            },
        },
        requirements=requirements,
        gaps=gaps,
    )

    disk_requirement = next(
        item for item in requirements if item.quota_name == "compute.disk.size.network-ssd"
    )
    assert disk_requirement.required == 93 * _GIB
    assert gaps == []


def test_evaluate_requirement_uses_matching_capacity_dashboard_row_for_gpu_quota() -> None:
    requirement = AggregatedQuotaRequirement(
        component_id="mk8s",
        instance_id="mk8s",
        component_label="mk8s",
        quota_name="compute.instance.gpu.b300",
        region="uk-south1",
        required=8,
        reason="1 GPU node(s) at gpu-b300-sxm/8gpu-192vcpu-2768gb",
        gpu_capacity_shape=GpuCapacityShape(
            platform="gpu-b300-sxm",
            preset="8gpu-192vcpu-2768gb",
            fabric="uk-south1-a",
            mode="regular",
            gpu_count_per_instance=8,
        ),
    )

    check = _evaluate_requirement(
        requirement,
        tenant_quotas={
            ("compute.instance.gpu.b300", "uk-south1"): QuotaRecord(
                name="compute.instance.gpu.b300",
                region="uk-south1",
                limit=0,
                usage=0,
                service="compute",
                description="NVIDIA B300 for regular VMs without reservations",
                unit="count",
                state="STATE_ACTIVE",
                usage_state="USAGE_STATE_NOT_USED",
                usage_percentage="0.00",
            )
        },
        project_quotas={},
        capacity_resource_advice=(
            _capacity_advice(
                region="uk-south1",
                platform="gpu-b300-sxm",
                preset="8gpu-192vcpu-2768gb",
                fabric="uk-south1-a",
                on_demand_available=1,
                on_demand_limit=2,
                on_demand_level="AVAILABILITY_LEVEL_MEDIUM",
            ),
        ),
    )

    assert check.available == 8
    assert check.sufficient is True
    assert check.source_scope == "capacity-dashboard/on-demand"
    assert check.description == (
        "Capacity Dashboard GPU availability "
        "(regular-vm slots, fabric uk-south1-a, converted to GPU units)"
    )


def test_evaluate_requirement_converts_capacity_dashboard_vm_slots_to_gpu_units() -> None:
    requirement = AggregatedQuotaRequirement(
        component_id="mk8s",
        instance_id="mk8s",
        component_label="mk8s",
        quota_name="compute.instance.gpu.h100",
        region="eu-north1",
        required=16,
        reason="2 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb",
        gpu_capacity_shape=GpuCapacityShape(
            platform="gpu-h100-sxm",
            preset="8gpu-128vcpu-1600gb",
            fabric="fabric-6",
            mode="regular",
            gpu_count_per_instance=8,
        ),
    )

    check = _evaluate_requirement(
        requirement,
        tenant_quotas={},
        project_quotas={},
        capacity_resource_advice=(
            _capacity_advice(
                region="eu-north1",
                platform="gpu-h100-sxm",
                preset="8gpu-128vcpu-1600gb",
                fabric="fabric-6",
                reserved_available=3,
                reserved_limit=3,
                reserved_level="AVAILABILITY_LEVEL_HIGH",
            ),
        ),
    )

    assert check.available == 24
    assert check.sufficient is True
    assert check.source_scope == "capacity-dashboard/reserved"
    assert check.description == (
        "Capacity Dashboard GPU availability "
        "(reserved VM slots, fabric fabric-6, converted to GPU units)"
    )


def test_evaluate_requirement_auto_reservation_policy_can_mix_capacity_lanes() -> None:
    requirement = AggregatedQuotaRequirement(
        component_id="mk8s",
        instance_id="mk8s",
        component_label="mk8s",
        quota_name="compute.instance.gpu.h100",
        region="eu-north1",
        required=16,
        reason=(
            "2 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb for 'worker' "
            "(reservation policy AUTO)"
        ),
        gpu_capacity_shape=GpuCapacityShape(
            platform="gpu-h100-sxm",
            preset="8gpu-128vcpu-1600gb",
            fabric="fabric-6",
            mode="auto",
            gpu_count_per_instance=8,
        ),
    )

    check = _evaluate_requirement(
        requirement,
        tenant_quotas={},
        project_quotas={},
        capacity_resource_advice=(
            _capacity_advice(
                region="eu-north1",
                platform="gpu-h100-sxm",
                preset="8gpu-128vcpu-1600gb",
                fabric="fabric-6",
                on_demand_available=1,
                on_demand_limit=2,
                on_demand_level="AVAILABILITY_LEVEL_MEDIUM",
                reserved_available=1,
                reserved_limit=2,
                reserved_level="AVAILABILITY_LEVEL_HIGH",
            ),
        ),
    )

    assert check.available == 16
    assert check.sufficient is True
    assert check.source_scope == "capacity-dashboard/auto"
    assert check.description == (
        "Capacity Dashboard GPU availability "
        "(AUTO reservation policy: reserved + regular-vm slots, fabric fabric-6, "
        "converted to GPU units)"
    )


def test_evaluate_requirement_forbid_reservation_policy_checks_common_pool_only() -> None:
    requirement = AggregatedQuotaRequirement(
        component_id="mk8s",
        instance_id="mk8s",
        component_label="mk8s",
        quota_name="compute.instance.gpu.h100",
        region="eu-north1",
        required=8,
        reason=(
            "1 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb for 'worker' "
            "(reservation policy FORBID)"
        ),
        gpu_capacity_shape=GpuCapacityShape(
            platform="gpu-h100-sxm",
            preset="8gpu-128vcpu-1600gb",
            fabric="fabric-6",
            mode="on-demand",
            gpu_count_per_instance=8,
        ),
    )

    check = _evaluate_requirement(
        requirement,
        tenant_quotas={},
        project_quotas={},
        capacity_resource_advice=(
            _capacity_advice(
                region="eu-north1",
                platform="gpu-h100-sxm",
                preset="8gpu-128vcpu-1600gb",
                fabric="fabric-6",
                on_demand_available=0,
                on_demand_limit=2,
                reserved_available=2,
                reserved_limit=2,
                reserved_level="AVAILABILITY_LEVEL_HIGH",
            ),
        ),
    )

    assert check.available == 0
    assert check.sufficient is False
    assert check.source_scope == "capacity-dashboard/on-demand"
    assert check.description == (
        "Capacity Dashboard GPU availability "
        "(FORBID reservation policy: regular-vm slots, fabric fabric-6, "
        "converted to GPU units)"
    )


@pytest.mark.parametrize(
    (
        "mode",
        "on_demand_data_state",
        "reserved_data_state",
        "expected_scope",
        "expected_state_detail",
    ),
    (
        (
            "auto",
            "DATA_STATE_FRESH",
            "DATA_STATE_STALE",
            "capacity-dashboard/auto",
            "reserved=DATA_STATE_STALE, on-demand=DATA_STATE_FRESH",
        ),
        (
            "reserved",
            "DATA_STATE_FRESH",
            "DATA_STATE_UNKNOWN",
            "capacity-dashboard/reserved",
            "reserved=DATA_STATE_UNKNOWN",
        ),
        (
            "on-demand",
            "",
            "DATA_STATE_FRESH",
            "capacity-dashboard/on-demand",
            "on-demand=DATA_STATE_UNSPECIFIED",
        ),
    ),
)
def test_evaluate_requirement_marks_non_fresh_selected_capacity_policy_lane_unknown(
    mode: str,
    on_demand_data_state: str,
    reserved_data_state: str,
    expected_scope: str,
    expected_state_detail: str,
) -> None:
    requirement = AggregatedQuotaRequirement(
        component_id="mk8s",
        instance_id="mk8s",
        component_label="mk8s",
        quota_name="compute.instance.gpu.h100",
        region="eu-north1",
        required=8,
        reason="GPU capacity freshness check",
        gpu_capacity_shape=GpuCapacityShape(
            platform="gpu-h100-sxm",
            preset="8gpu-128vcpu-1600gb",
            fabric="fabric-6",
            mode=mode,
            gpu_count_per_instance=8,
        ),
    )

    check = _evaluate_requirement(
        requirement,
        tenant_quotas={},
        project_quotas={},
        capacity_resource_advice=(
            _capacity_advice(
                region="eu-north1",
                platform="gpu-h100-sxm",
                preset="8gpu-128vcpu-1600gb",
                fabric="fabric-6",
                on_demand_available=2,
                on_demand_data_state=on_demand_data_state,
                reserved_available=2,
                reserved_data_state=reserved_data_state,
            ),
        ),
    )

    assert check.available is None
    assert check.sufficient is None
    assert check.source_scope == expected_scope
    assert "Capacity Dashboard GPU availability is unknown" in check.description
    assert expected_state_detail in check.description


@pytest.mark.parametrize(
    ("mode", "on_demand_data_state", "reserved_data_state", "expected_scope"),
    (
        (
            "reserved",
            "DATA_STATE_STALE",
            "DATA_STATE_FRESH",
            "capacity-dashboard/reserved",
        ),
        (
            "on-demand",
            "DATA_STATE_FRESH",
            "DATA_STATE_STALE",
            "capacity-dashboard/on-demand",
        ),
    ),
)
def test_evaluate_requirement_ignores_non_selected_capacity_policy_lane_freshness(
    mode: str,
    on_demand_data_state: str,
    reserved_data_state: str,
    expected_scope: str,
) -> None:
    requirement = AggregatedQuotaRequirement(
        component_id="mk8s",
        instance_id="mk8s",
        component_label="mk8s",
        quota_name="compute.instance.gpu.h100",
        region="eu-north1",
        required=8,
        reason="GPU capacity freshness check",
        gpu_capacity_shape=GpuCapacityShape(
            platform="gpu-h100-sxm",
            preset="8gpu-128vcpu-1600gb",
            fabric="fabric-6",
            mode=mode,
            gpu_count_per_instance=8,
        ),
    )

    check = _evaluate_requirement(
        requirement,
        tenant_quotas={},
        project_quotas={},
        capacity_resource_advice=(
            _capacity_advice(
                region="eu-north1",
                platform="gpu-h100-sxm",
                preset="8gpu-128vcpu-1600gb",
                fabric="fabric-6",
                on_demand_available=1,
                on_demand_data_state=on_demand_data_state,
                reserved_available=1,
                reserved_data_state=reserved_data_state,
            ),
        ),
    )

    assert check.available == 8
    assert check.sufficient is True
    assert check.source_scope == expected_scope


def test_evaluate_requirement_requires_exact_selected_gpu_fabric() -> None:
    requirement = AggregatedQuotaRequirement(
        component_id="mk8s",
        instance_id="mk8s",
        component_label="mk8s",
        quota_name="compute.instance.gpu.h100",
        region="eu-north1",
        required=16,
        reason="2 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb",
        gpu_capacity_shape=GpuCapacityShape(
            platform="gpu-h100-sxm",
            preset="8gpu-128vcpu-1600gb",
            fabric="fabric-4",
            mode="regular",
            gpu_count_per_instance=8,
        ),
    )

    check = _evaluate_requirement(
        requirement,
        tenant_quotas={},
        project_quotas={},
        capacity_resource_advice=(
            _capacity_advice(
                region="eu-north1",
                platform="gpu-h100-sxm",
                preset="8gpu-128vcpu-1600gb",
                fabric="fabric-6",
                reserved_available=3,
                reserved_limit=3,
                reserved_level="AVAILABILITY_LEVEL_HIGH",
            ),
        ),
    )

    assert check.available == 0
    assert check.sufficient is False
    assert check.source_scope == "capacity-dashboard"
    assert check.description == (
        "Capacity Dashboard reported no matching GPU shape row for "
        "gpu-h100-sxm/8gpu-128vcpu-1600gb, fabric fabric-4"
    )


def test_evaluate_requirement_picks_best_capacity_dashboard_row_when_fabric_is_not_fixed() -> None:
    requirement = AggregatedQuotaRequirement(
        component_id="mk8s",
        instance_id="mk8s",
        component_label="mk8s",
        quota_name="compute.instance.gpu.h100",
        region="eu-north1",
        required=16,
        reason="2 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb",
        gpu_capacity_shape=GpuCapacityShape(
            platform="gpu-h100-sxm",
            preset="8gpu-128vcpu-1600gb",
            fabric="",
            mode="regular",
            gpu_count_per_instance=8,
        ),
    )

    check = _evaluate_requirement(
        requirement,
        tenant_quotas={},
        project_quotas={},
        capacity_resource_advice=(
            _capacity_advice(
                region="eu-north1",
                platform="gpu-h100-sxm",
                preset="8gpu-128vcpu-1600gb",
                fabric="fabric-4",
                on_demand_available=0,
                on_demand_limit=32,
                on_demand_level="AVAILABILITY_LEVEL_LOW",
            ),
            _capacity_advice(
                region="eu-north1",
                platform="gpu-h100-sxm",
                preset="8gpu-128vcpu-1600gb",
                fabric="fabric-2",
                on_demand_available=2,
                on_demand_limit=32,
                on_demand_level="AVAILABILITY_LEVEL_MEDIUM",
            ),
        ),
    )

    assert check.available == 16
    assert check.sufficient is True
    assert check.source_scope == "capacity-dashboard/on-demand"
    assert check.description == (
        "Capacity Dashboard GPU availability "
        "(regular-vm slots, fabric fabric-2, converted to GPU units)"
    )


def test_evaluate_requirement_uses_preemptible_capacity_dashboard_lane_for_gpu_quota() -> None:
    requirement = AggregatedQuotaRequirement(
        component_id="vm",
        instance_id="vm",
        component_label="vm",
        quota_name="compute.instance.gpu.h100",
        region="eu-north1",
        required=1,
        reason="1 preemptible GPU(s) from gpu-h100-sxm/1gpu-16vcpu-200gb",
        gpu_capacity_shape=GpuCapacityShape(
            platform="gpu-h100-sxm",
            preset="1gpu-16vcpu-200gb",
            fabric="",
            mode="preemptible",
            gpu_count_per_instance=1,
        ),
    )

    check = _evaluate_requirement(
        requirement,
        tenant_quotas={},
        project_quotas={},
        capacity_resource_advice=(
            _capacity_advice(
                region="eu-north1",
                platform="gpu-h100-sxm",
                preset="1gpu-16vcpu-200gb",
                fabric="fabric-2",
                preemptible_available=3,
                preemptible_limit=128,
                preemptible_level="AVAILABILITY_LEVEL_HIGH",
            ),
        ),
    )

    assert check.available == 3
    assert check.sufficient is True
    assert check.source_scope == "capacity-dashboard/preemptible"


def test_evaluate_requirement_marks_gpu_quota_unknown_when_capacity_dashboard_lookup_fails() -> (
    None
):
    requirement = AggregatedQuotaRequirement(
        component_id="mk8s",
        instance_id="mk8s",
        component_label="mk8s",
        quota_name="compute.instance.gpu.b300",
        region="uk-south1",
        required=8,
        reason="1 GPU node(s) at gpu-b300-sxm/8gpu-192vcpu-2768gb",
        gpu_capacity_shape=GpuCapacityShape(
            platform="gpu-b300-sxm",
            preset="8gpu-192vcpu-2768gb",
            fabric="uk-south1-a",
            mode="regular",
            gpu_count_per_instance=8,
        ),
    )

    check = _evaluate_requirement(
        requirement,
        tenant_quotas={},
        project_quotas={},
        capacity_resource_advice=None,
    )

    assert check.available is None
    assert check.sufficient is None
    assert check.source_scope == "unresolved"


def test_assess_live_quota_requirements_reports_explicit_sufficiency_and_shortage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Session:
        def __init__(self, *, context: str, project_id: str) -> None:
            assert context == "onboarded soperator upgrade quota preflight"
            assert project_id == "project-1"
            self.closed = False

        def list_quotas(self, *, parent_id: str) -> dict[tuple[str, str], QuotaRecord]:
            if parent_id == "tenant-1":
                return {
                    ("compute.instance.count", "eu-north1"): _quota_record(
                        name="compute.instance.count",
                        limit=10,
                        usage=1,
                    ),
                    ("compute.filesystem.count", "eu-north1"): _quota_record(
                        name="compute.filesystem.count",
                        limit=1,
                        usage=0,
                    ),
                }
            if parent_id == "project-1":
                return {
                    ("compute.instance.count", "eu-north1"): _quota_record(
                        name="compute.instance.count",
                        limit=5,
                        usage=1,
                    ),
                    ("compute.filesystem.count", "eu-north1"): _quota_record(
                        name="compute.filesystem.count",
                        limit=1,
                        usage=0,
                    ),
                }
            raise AssertionError(parent_id)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(quota_checks, "_QuotaSession", _Session)

    report = assess_live_quota_requirements(
        tenant_id="tenant-1",
        project_id="project-1",
        region_id="eu-north1",
        context="onboarded soperator upgrade quota preflight",
        requirements=[
            QuotaRequirement(
                component_id="mk8s",
                instance_id="onboarded",
                component_label="onboarded worker remediation",
                quota_name="compute.instance.count",
                region="eu-north1",
                required=4,
                reason="preserved worker node-template rollout",
            ),
            QuotaRequirement(
                component_id="sfs",
                instance_id="aligned-sfs",
                component_label="aligned SFS",
                quota_name="compute.filesystem.count",
                region="eu-north1",
                required=2,
                reason="target SFS filesystems",
            ),
        ],
    )

    checks = {item.quota_name: item for item in report.checks}
    assert report.errors == ()
    assert checks["compute.instance.count"].available == 4
    assert checks["compute.instance.count"].sufficient is True
    assert checks["compute.filesystem.count"].available == 1
    assert checks["compute.filesystem.count"].sufficient is False


def test_assess_live_quota_requirements_allows_project_only_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _Session:
        def __init__(self, *, context: str, project_id: str) -> None:
            assert context == "quota assessment"
            assert project_id == "project-1"

        def list_quotas(self, *, parent_id: str) -> dict[tuple[str, str], QuotaRecord]:
            calls.append(parent_id)
            assert parent_id == "project-1"
            return {
                ("compute.instance.count", "eu-north1"): _quota_record(
                    name="compute.instance.count",
                    limit=5,
                    usage=1,
                )
            }

        def close(self) -> None:
            pass

    monkeypatch.setattr(quota_checks, "_QuotaSession", _Session)

    report = assess_live_quota_requirements(
        tenant_id="",
        project_id="project-1",
        region_id="eu-north1",
        requirements=[
            QuotaRequirement(
                component_id="mk8s",
                instance_id="external",
                component_label="external worker remediation",
                quota_name="compute.instance.count",
                region="eu-north1",
                required=4,
                reason="preserved worker node-template rollout",
            ),
        ],
    )

    assert calls == ["project-1"]
    assert report.errors == (
        "tenant_id was not provided; tenant quota and Capacity Dashboard checks were "
        "skipped, but project quota checks still ran",
    )
    assert len(report.checks) == 1
    assert report.checks[0].available == 4
    assert report.checks[0].sufficient is True
    assert report.checks[0].source_scope == "project"


def test_assess_live_quota_requirements_requires_project_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unexpected_session(**_kwargs: object) -> object:
        raise AssertionError("quota session should not be opened without identity")

    monkeypatch.setattr(quota_checks, "_QuotaSession", _unexpected_session)

    report = assess_live_quota_requirements(
        tenant_id="tenant-1",
        project_id="",
        region_id="eu-north1",
        requirements=(),
    )

    assert report.checks == ()
    assert report.errors == ("quota assessment is missing project_id",)


def test_assess_live_quota_requirements_preserves_gaps_and_lookup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Session:
        def __init__(self, *, context: str, project_id: str) -> None:
            del context, project_id

        def list_quotas(self, *, parent_id: str) -> dict[tuple[str, str], QuotaRecord]:
            if parent_id == "tenant-1":
                raise RuntimeError("tenant lookup unavailable")
            return {
                ("compute.disk.count", "eu-north1"): _quota_record(
                    name="compute.disk.count",
                    limit=3,
                    usage=1,
                )
            }

        def close(self) -> None:
            pass

    monkeypatch.setattr(quota_checks, "_QuotaSession", _Session)
    gap = QuotaCoverageGap(
        component_id="mk8s",
        instance_id="external",
        component_label="external MK8s",
        message="boot-disk size could not be resolved",
    )

    report = assess_live_quota_requirements(
        tenant_id="tenant-1",
        project_id="project-1",
        region_id="eu-north1",
        requirements=[
            QuotaRequirement(
                component_id="mk8s",
                instance_id="external",
                component_label="external MK8s",
                quota_name="compute.disk.count",
                region="eu-north1",
                required=2,
                reason="replacement node boot disks",
            )
        ],
        coverage_gaps=(gap,),
    )

    assert report.coverage_gaps == (gap,)
    assert report.checks[0].sufficient is True
    assert report.errors == ("tenant quota lookup failed for tenant-1: tenant lookup unavailable",)


def test_assess_live_quota_requirements_uses_capacity_dashboard_for_gpu_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Session:
        def __init__(self, *, context: str, project_id: str) -> None:
            del context, project_id

        def list_quotas(self, *, parent_id: str) -> dict[tuple[str, str], QuotaRecord]:
            del parent_id
            return {}

        def list_capacity_resource_advice(
            self,
            *,
            parent_id: str,
        ) -> tuple[CapacityResourceAdvice, ...]:
            assert parent_id == "tenant-1"
            return (
                _capacity_advice(
                    region="eu-north1",
                    platform="gpu-h100-sxm",
                    preset="8gpu-128vcpu-1600gb",
                    fabric="fabric-6",
                    reserved_available=2,
                    reserved_limit=4,
                    reserved_level="AVAILABILITY_LEVEL_HIGH",
                ),
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(quota_checks, "_QuotaSession", _Session)

    report = assess_live_quota_requirements(
        tenant_id="tenant-1",
        project_id="project-1",
        region_id="eu-north1",
        requirements=[
            QuotaRequirement(
                component_id="mk8s",
                instance_id="external",
                component_label="external GPU workers",
                quota_name="compute.instance.gpu.h100",
                region="eu-north1",
                required=16,
                reason="2 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb",
                gpu_capacity_shape=GpuCapacityShape(
                    platform="gpu-h100-sxm",
                    preset="8gpu-128vcpu-1600gb",
                    fabric="fabric-6",
                    mode="regular",
                    gpu_count_per_instance=8,
                ),
            )
        ],
    )

    assert report.errors == ()
    assert len(report.checks) == 1
    check = report.checks[0]
    assert check.available == 16
    assert check.sufficient is True
    assert check.source_scope == "capacity-dashboard/reserved"


def test_plan_quota_request_changes_targets_the_constraining_scopes() -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-18T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                quota_name="compute.disk.size.network-ssd",
                region="eu-north1",
                required=200,
                reason="mk8s boot disks",
                unit="byte",
                available=0,
                sufficient=False,
                tenant_limit=100,
                tenant_usage=100,
                project_limit=150,
                project_usage=100,
                source_scope="tenant+project",
                description="SSD quota",
                tenant_quota_id="tenant-quota-1",
                project_quota_id="project-quota-1",
            ),
        ),
    )

    changes = plan_quota_request_changes(report)

    assert changes == (
        QuotaRequestChange(
            container_id="project-456",
            container_scope="project",
            quota_name="compute.disk.size.network-ssd",
            region="eu-north1",
            current_limit=150,
            current_usage=100,
            required=200,
            requested_limit=300,
            unit="byte",
        ),
        QuotaRequestChange(
            container_id="tenant-123",
            container_scope="tenant",
            quota_name="compute.disk.size.network-ssd",
            region="eu-north1",
            current_limit=100,
            current_usage=100,
            required=200,
            requested_limit=300,
            unit="byte",
        ),
    )


def test_plan_quota_request_changes_coalesces_split_checks_for_one_quota_scope() -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="uk-south1",
        checked_at="2026-04-18T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s-a",
                instance_id="mk8s-a",
                component_label="mk8s-a",
                quota_name="compute.instance.gpu.b300",
                region="uk-south1",
                required=8,
                reason="fabric a",
                unit="count",
                available=0,
                sufficient=False,
                tenant_limit=0,
                tenant_usage=0,
                project_limit=None,
                project_usage=None,
                source_scope="tenant",
                description="GPU quota",
                tenant_quota_id="tenant-gpu-quota",
            ),
            QuotaCheck(
                component_id="mk8s-b",
                instance_id="mk8s-b",
                component_label="mk8s-b",
                quota_name="compute.instance.gpu.b300",
                region="uk-south1",
                required=16,
                reason="fabric b",
                unit="count",
                available=0,
                sufficient=False,
                tenant_limit=0,
                tenant_usage=0,
                project_limit=None,
                project_usage=None,
                source_scope="tenant",
                description="GPU quota",
                tenant_quota_id="tenant-gpu-quota",
            ),
        ),
    )

    changes = plan_quota_request_changes(report)

    assert len(changes) == 1
    change = changes[0]
    assert change.container_scope == "tenant"
    assert change.container_id == "tenant-123"
    assert change.quota_name == "compute.instance.gpu.b300"
    assert change.required == 24
    assert change.requested_limit == 24


def test_format_quota_request_manual_followup_lines_includes_minimum_target() -> None:
    lines = format_quota_request_manual_followup_lines(
        (
            QuotaRequestChange(
                container_id="tenant-123",
                container_scope="tenant",
                quota_name="compute.disk.size.network-ssd",
                region="eu-north1",
                current_limit=0,
                current_usage=0,
                required=2396591751168,
                requested_limit=2396591751168,
                unit="byte",
            ),
        )
    )

    assert lines == [
        "  - tenant tenant-123: eu-north1 compute.disk.size.network-ssd "
        "-> request total limit at least 2.2 TiB (2396591751168 byte) "
        "(increase by at least 2.2 TiB (2396591751168 byte) over current limit 0 B)"
    ]


def test_request_quota_changes_collects_permission_denied_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="uk-south1",
        checked_at="2026-04-18T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                quota_name="compute.disk.size.network-ssd",
                region="uk-south1",
                required=1024,
                reason="mk8s boot disks",
                unit="byte",
                available=0,
                sufficient=False,
                tenant_limit=0,
                tenant_usage=0,
                project_limit=None,
                project_usage=None,
                source_scope="tenant",
                description="SSD quota",
                tenant_quota_id="tenant-quota-1",
            ),
        ),
    )

    class _Session:
        def __init__(self, *, context: str, project_id: str) -> None:
            assert context == "quota request"
            assert project_id == "project-456"

        def recommend_quota_request_changes(
            self, changes: tuple[QuotaRequestChange, ...]
        ) -> tuple[QuotaRequestChange, ...]:
            return changes

        def submit_quota_requests(self, _changes: tuple[QuotaRequestChange, ...]) -> None:
            raise RuntimeError(
                "Failed to create quota requests: Request error PERMISSION_DENIED: Permission denied; "
                "Caused by error: UnauthorizedMany"
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(quota_checks, "_QuotaSession", _Session)

    result = request_quota_changes(report, context="quota request")

    assert len(result.planned_changes) == 1
    assert result.submitted_changes == ()
    assert len(result.permission_denied_failures) == 1
    assert result.permission_denied_failures[0].change.container_id == "tenant-123"


def test_request_quota_changes_falls_back_to_manual_when_internal_api_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="uk-south1",
        checked_at="2026-04-18T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                quota_name="compute.disk.size.network-ssd",
                region="uk-south1",
                required=1024,
                reason="mk8s boot disks",
                unit="byte",
                available=0,
                sufficient=False,
                tenant_limit=0,
                tenant_usage=0,
                project_limit=None,
                project_usage=None,
                source_scope="tenant",
                description="SSD quota",
                tenant_quota_id="tenant-quota-1",
            ),
        ),
    )

    class _Session:
        def __init__(self, *, context: str, project_id: str) -> None:
            assert context == "quota request"
            assert project_id == "project-456"

        def recommend_quota_request_changes(
            self, _changes: tuple[QuotaRequestChange, ...]
        ) -> tuple[QuotaRequestChange, ...]:
            raise _NpcQuotaRequestUnavailableError("internal quota-request API unavailable")

        def close(self) -> None:
            return None

    monkeypatch.setattr(quota_checks, "_QuotaSession", _Session)

    result = request_quota_changes(report, context="quota request")

    assert len(result.planned_changes) == 1
    assert result.submitted_changes == ()
    assert result.unavailable_reason == "internal quota-request API unavailable"


def test_request_quota_changes_raises_non_permission_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="uk-south1",
        checked_at="2026-04-18T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                quota_name="compute.disk.size.network-ssd",
                region="uk-south1",
                required=1024,
                reason="mk8s boot disks",
                unit="byte",
                available=0,
                sufficient=False,
                tenant_limit=0,
                tenant_usage=0,
                project_limit=None,
                project_usage=None,
                source_scope="tenant",
                description="SSD quota",
                tenant_quota_id="tenant-quota-1",
            ),
        ),
    )

    class _Session:
        def __init__(self, *, context: str, project_id: str) -> None:
            assert context == "quota request"
            assert project_id == "project-456"

        def recommend_quota_request_changes(
            self, changes: tuple[QuotaRequestChange, ...]
        ) -> tuple[QuotaRequestChange, ...]:
            return changes

        def submit_quota_requests(self, _changes: tuple[QuotaRequestChange, ...]) -> None:
            raise RuntimeError("Failed to create quota requests: deadline exceeded")

        def close(self) -> None:
            return None

    monkeypatch.setattr(quota_checks, "_QuotaSession", _Session)

    with pytest.raises(RuntimeError, match="deadline exceeded"):
        request_quota_changes(report, context="quota request")


def test_format_quota_report_lines_include_errors_gaps_and_shortages() -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-10T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="ssh-jumphost",
                instance_id="ssh-jumphost",
                component_label="ssh-jumphost",
                quota_name="compute.instance.count",
                region="eu-north1",
                required=1,
                reason="one VM",
                unit="",
                available=0,
                sufficient=False,
                tenant_limit=0,
                tenant_usage=0,
                project_limit=None,
                project_usage=0,
                source_scope="tenant",
                description="VM count",
                contributors=(),
            ),
        ),
        coverage_gaps=(
            QuotaCoverageGap(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                message=(
                    "MK8s CPU node-group boot-disk quota could not be fully evaluated; "
                    "set inputs.node_groups.<cpu-group>.boot_disk.type and "
                    "inputs.node_groups.<cpu-group>.boot_disk.size_gibibytes"
                ),
            ),
            QuotaCoverageGap(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                message=(
                    "MK8s GPU node-group boot-disk quota could not be fully evaluated; "
                    "set inputs.node_groups.<gpu-group>.boot_disk.type and "
                    "inputs.node_groups.<gpu-group>.boot_disk.size_gibibytes"
                ),
            ),
        ),
        errors=("tenant quota lookup failed",),
    )

    lines = format_quota_report_lines(report, phase="render")

    assert lines[0].startswith(f"[{WARNING_COLOR}]Quota check warning:[/]")
    assert "tenant quota lookup failed" in lines[1]
    assert any("render detected insufficient Nebius quota" in line for line in lines)
    assert any("compute.instance.count requires 1, available 0" in line for line in lines)
    assert any(
        "quota could not be fully evaluated for the following component(s)" in line
        for line in lines
    )
    assert "  - mk8s" in lines
    assert "    gaps:" in lines
    assert (
        "      - MK8s CPU node-group boot-disk quota could not be fully evaluated; "
        "set inputs.node_groups.<cpu-group>.boot_disk.type and "
        "inputs.node_groups.<cpu-group>.boot_disk.size_gibibytes"
    ) in lines
    assert (
        "      - MK8s GPU node-group boot-disk quota could not be fully evaluated; "
        "set inputs.node_groups.<gpu-group>.boot_disk.type and "
        "inputs.node_groups.<gpu-group>.boot_disk.size_gibibytes"
    ) in lines


def test_format_quota_report_lines_marks_capacity_dashboard_shortages() -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-10T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s-node-template-surge-worker",
                component_label="mk8s@worker surge",
                quota_name="compute.instance.gpu.h100",
                region="eu-north1",
                required=8,
                reason=(
                    "1 GPU node(s) at gpu-h100-sxm/8gpu-128vcpu-1600gb for "
                    "'worker-safe-surge' (reservation policy AUTO)"
                ),
                unit="count",
                available=0,
                sufficient=False,
                tenant_limit=32,
                tenant_usage=8,
                project_limit=None,
                project_usage=8,
                source_scope="capacity-dashboard/auto",
                description=(
                    "Capacity Dashboard GPU availability "
                    "(AUTO reservation policy: reserved + regular-vm slots, "
                    "fabric fabric-6, converted to GPU units)"
                ),
                contributors=(),
            ),
        ),
    )

    lines = format_quota_report_lines(report, phase="node-template safe-surge")

    assert any("detected insufficient Nebius quota/capacity" in line for line in lines)
    assert any(
        "available 0 via Capacity Dashboard GPU availability "
        "(AUTO reservation policy: reserved + regular-vm slots, fabric fabric-6" in line
        for line in lines
    )


def test_format_quota_report_lines_can_disable_rich_markup_for_exception_text() -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-10T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s-node-template-surge-worker",
                component_label="mk8s@worker surge",
                quota_name="compute.instance.gpu.h100",
                region="eu-north1",
                required=8,
                reason="1 GPU surge node",
                unit="count",
                available=0,
                sufficient=False,
                tenant_limit=32,
                tenant_usage=8,
                project_limit=None,
                project_usage=8,
                source_scope="capacity-dashboard/auto",
                description="Capacity Dashboard GPU availability",
                contributors=(),
            ),
        ),
    )

    lines = format_quota_report_lines(
        report,
        phase="node-template safe-surge",
        markup=False,
    )

    assert lines[0].startswith("Quota warning:")
    assert "[#ffbf00]" not in "\n".join(lines)
    assert "[/]" not in "\n".join(lines)


def test_format_quota_report_lines_can_hide_coverage_gaps() -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-10T00:00:00+00:00",
        coverage_gaps=(
            QuotaCoverageGap(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                message=(
                    "MK8s CPU node-group boot-disk quota could not be fully evaluated; "
                    "set inputs.node_groups.<cpu-group>.boot_disk.type and "
                    "inputs.node_groups.<cpu-group>.boot_disk.size_gibibytes"
                ),
            ),
        ),
    )

    lines = format_quota_report_lines(
        report,
        phase="render",
        include_coverage_gaps=False,
    )

    assert lines == []


def test_quota_session_prefers_operator_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeSdk:
        def sync_close(self) -> None:
            return None

    monkeypatch.setattr(
        quota_checks,
        "init_nebius_sdk",
        lambda **kwargs: captured.update(kwargs) or _FakeSdk(),
    )

    session = quota_checks._QuotaSession(
        context="deploy quota assessment", project_id="project-456"
    )
    session.close()

    assert captured["parent_id"] == "project-456"
    assert captured["context"] == "deploy quota assessment"
    assert captured["prefer_operator_auth"] is True


def test_quota_session_does_not_cache_transient_preset_lookup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class _FakePlatformServiceClient:
        def __init__(self, sdk: object) -> None:
            self.sdk = sdk

        def get_by_name(self, request: object) -> object:
            nonlocal calls
            calls += 1
            raise RuntimeError("transient SDK failure")

    for module_name in (
        "nebius",
        "nebius.api",
        "nebius.api.nebius",
        "nebius.api.nebius.common",
        "nebius.api.nebius.compute",
    ):
        module = ModuleType(module_name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, module_name, module)
    common_v1: Any = ModuleType("nebius.api.nebius.common.v1")
    common_v1.GetByNameRequest = lambda **kwargs: kwargs
    compute_v1: Any = ModuleType("nebius.api.nebius.compute.v1")
    compute_v1.PlatformServiceClient = _FakePlatformServiceClient
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.common.v1", common_v1)
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.compute.v1", compute_v1)

    session = object.__new__(quota_checks._QuotaSession)
    session._sdk = object()
    session._context = "test"
    session._npc_path = ""
    session._preset_cache = {}
    session._capacity_resource_advice_cache = {}
    session._quota_cache = {}

    assert (
        session.preset_resources(
            project_id="project-123",
            platform="cpu-d3",
            preset="4vcpu-16gb",
        )
        is None
    )
    assert (
        session.preset_resources(
            project_id="project-123",
            platform="cpu-d3",
            preset="4vcpu-16gb",
        )
        is None
    )
    assert calls == 2
    assert session._preset_cache == {}


def test_format_quota_report_lines_include_unresolved_limits() -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-10T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                quota_name="mk8s.cluster.count",
                region="eu-north1",
                required=1,
                reason="one Managed Kubernetes cluster",
                unit="",
                available=None,
                sufficient=None,
                tenant_limit=None,
                tenant_usage=1,
                project_limit=None,
                project_usage=0,
                source_scope="unresolved",
                description="MK8s clusters",
                contributors=(),
            ),
        ),
    )

    lines = format_quota_report_lines(report, phase="quota check")

    assert any(
        "quota check could not resolve one or more live quota limits" in line for line in lines
    )
    assert any("mk8s.cluster.count requires 1, available unknown" in line for line in lines)


def test_format_quota_report_lines_include_confirmed_components_even_with_coverage_gaps() -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="eu-north1",
        checked_at="2026-04-10T00:00:00+00:00",
        checks=(
            QuotaCheck(
                component_id="multiple",
                instance_id="multiple",
                component_label="ssh-jumphost + 1 more",
                quota_name="compute.instance.count",
                region="eu-north1",
                required=2,
                reason="ssh-jumphost: one VM; object-storage: one helper VM",
                unit="",
                available=5,
                sufficient=True,
                tenant_limit=5,
                tenant_usage=0,
                project_limit=None,
                project_usage=0,
                source_scope="tenant",
                description="VM count",
                contributors=(
                    QuotaContributor(
                        component_id="ssh-jumphost",
                        instance_id="ssh-jumphost",
                        component_label="ssh-jumphost",
                        required=1,
                        reason="one VM",
                    ),
                    QuotaContributor(
                        component_id="object-storage",
                        instance_id="object-storage",
                        component_label="object-storage",
                        required=1,
                        reason="one helper VM",
                    ),
                ),
            ),
            QuotaCheck(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                quota_name="mk8s.cluster.count",
                region="eu-north1",
                required=1,
                reason="one Managed Kubernetes cluster",
                unit="",
                available=3,
                sufficient=True,
                tenant_limit=3,
                tenant_usage=0,
                project_limit=None,
                project_usage=0,
                source_scope="tenant",
                description="MK8s clusters",
                contributors=(),
            ),
        ),
        coverage_gaps=(
            QuotaCoverageGap(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                message=(
                    "MK8s CPU node-group boot-disk quota could not be fully evaluated; "
                    "set inputs.node_groups.<cpu-group>.boot_disk.type and "
                    "inputs.node_groups.<cpu-group>.boot_disk.size_gibibytes"
                ),
            ),
        ),
    )

    lines = format_quota_report_lines(
        report,
        phase="quota check",
        include_confirmed_components=True,
    )

    assert any(
        "live quota was sufficient for the following checked component(s)" in line for line in lines
    )
    assert "  - ssh-jumphost: 1 checked quota dimension confirmed in eu-north1" in lines
    assert "    checked:" in lines
    assert "      - compute.instance.count" in lines
    assert "  - object-storage: 1 checked quota dimension confirmed in eu-north1" in lines
    assert lines.count("      - compute.instance.count") == 2
    assert (
        "  - mk8s: 1 checked quota dimension confirmed in eu-north1 "
        "(partial coverage; see gaps below)"
    ) in lines
    assert "      - mk8s.cluster.count" in lines


def test_format_quota_report_lines_include_regional_availability() -> None:
    report = QuotaReport(
        tenant_id="tenant-123",
        project_id="project-456",
        region_id="us-central1",
        checked_at="2026-04-10T00:00:00+00:00",
        regional_availability=(
            RegionalQuotaAvailability(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                quota_name="compute.instance.gpu.b200",
                required=8,
                reason="mk8s: 1 GPU node(s) at gpu-b200-sxm/8gpu-160vcpu-1792gb",
                unit="count",
                current_region="us-central1",
                region_checks=(
                    QuotaCheck(
                        component_id="mk8s",
                        instance_id="mk8s",
                        component_label="mk8s",
                        quota_name="compute.instance.gpu.b200",
                        region="us-central1",
                        required=8,
                        reason="mk8s: 1 GPU node(s) at gpu-b200-sxm/8gpu-160vcpu-1792gb",
                        unit="count",
                        available=2,
                        sufficient=False,
                        tenant_limit=18,
                        tenant_usage=16,
                        project_limit=None,
                        project_usage=0,
                        source_scope="tenant",
                        description="B200 GPU quota",
                        contributors=(),
                    ),
                    QuotaCheck(
                        component_id="mk8s",
                        instance_id="mk8s",
                        component_label="mk8s",
                        quota_name="compute.instance.gpu.b200",
                        region="eu-north1",
                        required=8,
                        reason="mk8s: 1 GPU node(s) at gpu-b200-sxm/8gpu-160vcpu-1792gb",
                        unit="count",
                        available=18,
                        sufficient=True,
                        tenant_limit=18,
                        tenant_usage=0,
                        project_limit=None,
                        project_usage=0,
                        source_scope="tenant",
                        description="B200 GPU quota",
                        contributors=(),
                    ),
                ),
            ),
        ),
    )

    lines = format_quota_report_lines(report, phase="quota check")

    assert any("Regional quota availability for the current config shape" in line for line in lines)
    assert any("mk8s: compute.instance.gpu.b200 requires 8" in line for line in lines)
    assert any("us-central1 (current): available 2 (insufficient)" in line for line in lines)
    assert any("eu-north1: available 18 (sufficient)" in line for line in lines)


def test_estimate_vm_requirements_cover_regular_gpu_and_boot_disk() -> None:
    class _Session:
        def preset_resources(self, *, project_id: str, platform: str, preset: str):
            assert project_id == "project-1"
            assert platform == "gpu-h100-sxm"
            assert preset == "1gpu-16vcpu-200gb"
            return _resources(
                platform=platform,
                preset=preset,
                vcpu_count=16,
                memory_gibibytes=200,
                gpu_count=1,
            )

    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []

    _estimate_vm_requirements(
        session=cast(Any, _Session()),
        project_id="project-1",
        region="eu-north1",
        component_id="vm",
        instance_id="vm",
        inputs={
            "platform": "gpu-h100-sxm",
            "preset": "1gpu-16vcpu-200gb",
            "boot_disk_size_gib": 50,
            "boot_disk_type": "NETWORK_SSD",
            "public_ip_mode": "dynamic",
        },
        requirements=requirements,
        gaps=gaps,
    )

    quota_names = [item.quota_name for item in requirements]
    assert "compute.instance.count" in quota_names
    assert "compute.instance.gpu.h100" in quota_names
    assert "compute.disk.count" in quota_names
    assert "compute.disk.size.network-ssd" in quota_names
    assert "vpc.ipv4-address.public.count" in quota_names
    assert gaps == []


def test_estimate_vm_requirements_reports_missing_boot_disk_without_size_fallback() -> None:
    class _Session:
        def preset_resources(self, *, project_id: str, platform: str, preset: str):
            assert project_id == "project-1"
            return _resources(
                platform=platform,
                preset=preset,
                vcpu_count=4,
                memory_gibibytes=16,
                gpu_count=0,
            )

    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []

    _estimate_vm_requirements(
        session=cast(Any, _Session()),
        project_id="project-1",
        region="eu-north1",
        component_id="vm",
        instance_id="vm",
        inputs={
            "platform": "cpu-d3",
            "preset": "4vcpu-16gb",
            "public_ip_mode": "none",
        },
        requirements=requirements,
        gaps=gaps,
    )

    assert "compute.disk.count" in [item.quota_name for item in requirements]
    assert "compute.disk.size.network-ssd" not in [item.quota_name for item in requirements]
    assert len(gaps) == 1
    assert "Compute VM boot-disk quota could not be fully evaluated" in gaps[0].message


def test_estimate_vm_requirements_cover_preemptible_gpu_capacity() -> None:
    class _Session:
        def preset_resources(self, *, project_id: str, platform: str, preset: str):
            return _resources(
                platform=platform,
                preset=preset,
                vcpu_count=16,
                memory_gibibytes=200,
                gpu_count=1,
            )

    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []

    _estimate_vm_requirements(
        session=cast(Any, _Session()),
        project_id="project-1",
        region="us-central1",
        component_id="vm",
        instance_id="vm",
        inputs={
            "platform": "gpu-h100-sxm",
            "preset": "1gpu-16vcpu-200gb",
            "preemptible_enabled": True,
            "public_ip_mode": "none",
            "boot_disk_existing_id": "disk-existing",
        },
        requirements=requirements,
        gaps=gaps,
    )

    assert [item.quota_name for item in requirements] == [
        "compute.instance.preemptible.count",
        "compute.instance.gpu.h100",
    ]
    gpu_requirement = next(
        item for item in requirements if item.quota_name == "compute.instance.gpu.h100"
    )
    assert gpu_requirement.gpu_capacity_shape == GpuCapacityShape(
        platform="gpu-h100-sxm",
        preset="1gpu-16vcpu-200gb",
        fabric="",
        mode="preemptible",
        gpu_count_per_instance=1,
    )
    assert gaps == []
