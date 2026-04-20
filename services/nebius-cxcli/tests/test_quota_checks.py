from __future__ import annotations

import pytest

import nebius_cxcli.quota_checks as quota_checks
from nebius_cxcli.quota_checks import (
    AggregatedQuotaRequirement,
    CapacityBlockGroupAffinity,
    CapacityBlockGroupRecord,
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
    format_quota_report_lines,
    plan_quota_request_changes,
    request_quota_allowance_changes,
)
from nebius_cxcli.terminal_styles import WARNING_COLOR

_GIB = 1024 * 1024 * 1024


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


def test_aggregate_requirements_keep_capacity_block_group_affinity_separate() -> None:
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
                capacity_block_group_affinity=CapacityBlockGroupAffinity(
                    service="compute",
                    platform="gpu-b300-sxm",
                    fabric="uk-south1-a",
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
                capacity_block_group_affinity=CapacityBlockGroupAffinity(
                    service="compute",
                    platform="gpu-b300-sxm",
                    fabric="uk-south1-b",
                ),
            ),
        ]
    )

    assert len(aggregated) == 2
    assert {item.capacity_block_group_affinity for item in aggregated} == {
        CapacityBlockGroupAffinity(
            service="compute",
            platform="gpu-b300-sxm",
            fabric="uk-south1-a",
        ),
        CapacityBlockGroupAffinity(
            service="compute",
            platform="gpu-b300-sxm",
            fabric="uk-south1-b",
        ),
    }


def test_estimate_mk8s_requirements_tag_infiniband_gpu_checks_with_capacity_block_group_affinity() -> None:
    class _Session:
        def preset_resources(self, *, project_id: str, platform: str, preset: str):
            assert project_id == "project-1"
            assert platform == "gpu-b300-sxm"
            assert preset == "8gpu-192vcpu-2768gb"
            return type(
                "_Resources",
                (),
                {
                    "platform": platform,
                    "preset": preset,
                    "vcpu_count": 192,
                    "memory_gibibytes": 2768,
                    "gpu_count": 8,
                },
            )()

    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []

    _estimate_mk8s_requirements(
        session=_Session(),
        project_id="project-1",
        region="uk-south1",
        component_id="mk8s",
        instance_id="mk8s",
        inputs={
            "gpu_enabled": True,
            "gpu_node_groups": 1,
            "gpu_nodes_count_per_group": 1,
            "gpu_nodes_platform": "gpu-b300-sxm",
            "gpu_nodes_preset": "8gpu-192vcpu-2768gb",
            "infiniband_fabric": "uk-south1-a",
        },
        requirements=requirements,
        gaps=gaps,
    )

    affinity = CapacityBlockGroupAffinity(
        service="compute",
        platform="gpu-b300-sxm",
        fabric="uk-south1-a",
    )
    gpu_cluster_requirement = next(
        item for item in requirements if item.quota_name == "compute.gpucluster.count"
    )
    gpu_quota_requirement = next(
        item for item in requirements if item.quota_name == "compute.instance.gpu.b300"
    )

    assert gpu_cluster_requirement.capacity_block_group_affinity == affinity
    assert gpu_quota_requirement.capacity_block_group_affinity == affinity


def test_estimate_mk8s_requirements_cover_boot_disk_quota_from_explicit_inputs() -> None:
    class _Session:
        def preset_resources(self, *, project_id: str, platform: str, preset: str):
            if (project_id, platform, preset) == ("project-1", "cpu-d3", "32vcpu-128gb"):
                return type(
                    "_Resources",
                    (),
                    {
                        "platform": platform,
                        "preset": preset,
                        "vcpu_count": 32,
                        "memory_gibibytes": 128,
                        "gpu_count": 0,
                    },
                )()
            if (project_id, platform, preset) == ("project-1", "gpu-b300-sxm", "8gpu-192vcpu-2768gb"):
                return type(
                    "_Resources",
                    (),
                    {
                        "platform": platform,
                        "preset": preset,
                        "vcpu_count": 192,
                        "memory_gibibytes": 2768,
                        "gpu_count": 8,
                    },
                )()
            raise AssertionError((project_id, platform, preset))

    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []

    _estimate_mk8s_requirements(
        session=_Session(),
        project_id="project-1",
        region="uk-south1",
        component_id="mk8s",
        instance_id="mk8s",
        inputs={
            "cpu_nodes_count": 2,
            "cpu_nodes_platform": "cpu-d3",
            "cpu_nodes_preset": "32vcpu-128gb",
            "cpu_nodes_boot_disk_size_gib": 186,
            "cpu_nodes_boot_disk_type": "NETWORK_SSD",
            "gpu_enabled": True,
            "gpu_node_groups": 1,
            "gpu_nodes_count_per_group": 1,
            "gpu_nodes_platform": "gpu-b300-sxm",
            "gpu_nodes_preset": "8gpu-192vcpu-2768gb",
            "gpu_nodes_boot_disk_size_gib": 1023,
            "gpu_nodes_boot_disk_type": "NETWORK_SSD",
            "infiniband_fabric": "uk-south1-a",
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
            return type(
                "_Resources",
                (),
                {
                    "platform": platform,
                    "preset": preset,
                    "vcpu_count": 32,
                    "memory_gibibytes": 128,
                    "gpu_count": 0,
                },
            )()

    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []

    _estimate_mk8s_requirements(
        session=_Session(),
        project_id="project-1",
        region="uk-south1",
        component_id="mk8s",
        instance_id="mk8s",
        inputs={
            "cpu_nodes_count": 1,
            "cpu_nodes_platform": "cpu-d3",
            "cpu_nodes_preset": "32vcpu-128gb",
            "mk8s_cpu_node_group_overrides": {
                "template": {
                    "boot_disk": {
                        "size_gibibytes": 93,
                        "type": "NETWORK_SSD",
                    }
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


def test_evaluate_requirement_uses_matching_capacity_block_group_for_gpu_quota() -> None:
    requirement = AggregatedQuotaRequirement(
        component_id="mk8s",
        instance_id="mk8s",
        component_label="mk8s",
        quota_name="compute.instance.gpu.b300",
        region="uk-south1",
        required=8,
        reason="1 GPU node(s) at gpu-b300-sxm/8gpu-192vcpu-2768gb",
        capacity_block_group_affinity=CapacityBlockGroupAffinity(
            service="compute",
            platform="gpu-b300-sxm",
            fabric="uk-south1-a",
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
        capacity_block_groups=(
            CapacityBlockGroupRecord(
                id="capacityblockgroup-1",
                parent_id="tenant-1",
                region="uk-south1",
                service="compute",
                platform="gpu-b300-sxm",
                fabric="uk-south1-a",
                current_limit=8,
                usage=0,
                state="STATE_ACTIVE",
                usage_state="USAGE_STATE_NOT_USED",
                usage_percentage="0.00",
            ),
        ),
    )

    assert check.available == 8
    assert check.sufficient is True
    assert check.capacity_block_group_available == 8
    assert check.capacity_block_group_ids == ("capacityblockgroup-1",)
    assert "capacity-block-group" in check.source_scope


def test_evaluate_requirement_uses_matching_capacity_block_group_for_gpu_cluster_count() -> None:
    requirement = AggregatedQuotaRequirement(
        component_id="mk8s",
        instance_id="mk8s",
        component_label="mk8s",
        quota_name="compute.gpucluster.count",
        region="uk-south1",
        required=1,
        reason="one GPU cluster for InfiniBand fabric",
        capacity_block_group_affinity=CapacityBlockGroupAffinity(
            service="compute",
            platform="gpu-b300-sxm",
            fabric="uk-south1-a",
        ),
    )

    check = _evaluate_requirement(
        requirement,
        tenant_quotas={
            ("compute.gpucluster.count", "uk-south1"): QuotaRecord(
                name="compute.gpucluster.count",
                region="uk-south1",
                limit=0,
                usage=0,
                service="compute",
                description="Number of GPU clusters",
                unit="count",
                state="STATE_ACTIVE",
                usage_state="USAGE_STATE_NOT_USED",
                usage_percentage="0.00",
            )
        },
        project_quotas={},
        capacity_block_groups=(
            CapacityBlockGroupRecord(
                id="capacityblockgroup-1",
                parent_id="tenant-1",
                region="uk-south1",
                service="compute",
                platform="gpu-b300-sxm",
                fabric="uk-south1-a",
                current_limit=8,
                usage=0,
                state="STATE_ACTIVE",
                usage_state="USAGE_STATE_NOT_USED",
                usage_percentage="0.00",
            ),
        ),
    )

    assert check.available == 1
    assert check.sufficient is True
    assert check.capacity_block_group_available == 1


def test_evaluate_requirement_marks_capacity_backed_gpu_quota_unknown_when_capacity_lookup_fails() -> None:
    requirement = AggregatedQuotaRequirement(
        component_id="mk8s",
        instance_id="mk8s",
        component_label="mk8s",
        quota_name="compute.instance.gpu.b300",
        region="uk-south1",
        required=8,
        reason="1 GPU node(s) at gpu-b300-sxm/8gpu-192vcpu-2768gb",
        capacity_block_group_affinity=CapacityBlockGroupAffinity(
            service="compute",
            platform="gpu-b300-sxm",
            fabric="uk-south1-a",
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
        capacity_block_groups=None,
    )

    assert check.available is None
    assert check.sufficient is None
    assert check.source_scope == "unresolved"


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
            quota_id="project-quota-1",
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
            quota_id="tenant-quota-1",
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


def test_request_quota_allowance_changes_collects_permission_denied_failures(
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

        def request_quota_allowance_limit(self, **_kwargs: object) -> str:
            raise RuntimeError(
                "Failed to request quota 'compute.disk.size.network-ssd' in uk-south1 "
                "for tenant-123: Request error PERMISSION_DENIED: Permission denied; "
                "Caused by error: UnauthorizedMany"
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(quota_checks, "_QuotaSession", _Session)

    result = request_quota_allowance_changes(report, context="quota request")

    assert len(result.planned_changes) == 1
    assert result.submitted_changes == ()
    assert len(result.permission_denied_failures) == 1
    assert result.permission_denied_failures[0].change.container_id == "tenant-123"


def test_request_quota_allowance_changes_raises_non_permission_errors(
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

        def request_quota_allowance_limit(self, **_kwargs: object) -> str:
            raise RuntimeError("Failed to request quota 'compute.disk.size.network-ssd': deadline exceeded")

        def close(self) -> None:
            return None

    monkeypatch.setattr(quota_checks, "_QuotaSession", _Session)

    with pytest.raises(RuntimeError, match="deadline exceeded"):
        request_quota_allowance_changes(report, context="quota request")


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
                    "set inputs.cpu_nodes_boot_disk_size_gib and "
                    "inputs.cpu_nodes_boot_disk_type, or set "
                    "inputs.mk8s_cpu_node_group_overrides.template.boot_disk.size_* and "
                    "inputs.mk8s_cpu_node_group_overrides.template.boot_disk.type"
                ),
            ),
            QuotaCoverageGap(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                message=(
                    "MK8s GPU node-group boot-disk quota could not be fully evaluated; "
                    "set inputs.gpu_nodes_boot_disk_size_gib and "
                    "inputs.gpu_nodes_boot_disk_type, or set "
                    "inputs.mk8s_gpu_node_group_overrides.template.boot_disk.size_* and "
                    "inputs.mk8s_gpu_node_group_overrides.template.boot_disk.type"
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
        "set inputs.cpu_nodes_boot_disk_size_gib and inputs.cpu_nodes_boot_disk_type, "
        "or set inputs.mk8s_cpu_node_group_overrides.template.boot_disk.size_* and "
        "inputs.mk8s_cpu_node_group_overrides.template.boot_disk.type"
    ) in lines
    assert (
        "      - MK8s GPU node-group boot-disk quota could not be fully evaluated; "
        "set inputs.gpu_nodes_boot_disk_size_gib and inputs.gpu_nodes_boot_disk_type, "
        "or set inputs.mk8s_gpu_node_group_overrides.template.boot_disk.size_* and "
        "inputs.mk8s_gpu_node_group_overrides.template.boot_disk.type"
    ) in lines


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
                    "set inputs.cpu_nodes_boot_disk_size_gib and "
                    "inputs.cpu_nodes_boot_disk_type, or set "
                    "inputs.mk8s_cpu_node_group_overrides.template.boot_disk.size_* and "
                    "inputs.mk8s_cpu_node_group_overrides.template.boot_disk.type"
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

    assert any("quota check could not resolve one or more live quota limits" in line for line in lines)
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
                    "set inputs.cpu_nodes_boot_disk_size_gib and "
                    "inputs.cpu_nodes_boot_disk_type, or set "
                    "inputs.mk8s_cpu_node_group_overrides.template.boot_disk.size_* and "
                    "inputs.mk8s_cpu_node_group_overrides.template.boot_disk.type"
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
        "live quota was sufficient for the following checked component(s)"
        in line
        for line in lines
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
    assert any(
        "mk8s: compute.instance.gpu.b200 requires 8" in line
        for line in lines
    )
    assert any("us-central1 (current): available 2 (insufficient)" in line for line in lines)
    assert any("eu-north1: available 18 (sufficient)" in line for line in lines)


def test_estimate_vm_requirements_cover_regular_gpu_and_boot_disk() -> None:
    class _Session:
        def preset_resources(self, *, project_id: str, platform: str, preset: str):
            assert project_id == "project-1"
            assert platform == "gpu-h100-sxm"
            assert preset == "1gpu-16vcpu-200gb"
            return type(
                "_Resources",
                (),
                {"platform": platform, "preset": preset, "vcpu_count": 16, "memory_gibibytes": 200, "gpu_count": 1},
            )()

    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []

    _estimate_vm_requirements(
        session=_Session(),
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


def test_estimate_vm_requirements_report_preemptible_gpu_quota_gap() -> None:
    class _Session:
        def preset_resources(self, *, project_id: str, platform: str, preset: str):
            return type(
                "_Resources",
                (),
                {"platform": platform, "preset": preset, "vcpu_count": 16, "memory_gibibytes": 200, "gpu_count": 1},
            )()

    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []

    _estimate_vm_requirements(
        session=_Session(),
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

    assert [item.quota_name for item in requirements] == ["compute.instance.preemptible.count"]
    assert any(
        "GPU-type quota mapping for preemptible standalone VMs is not exposed" in gap.message
        for gap in gaps
    )
