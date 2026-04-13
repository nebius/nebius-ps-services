from __future__ import annotations

from nebius_cxcli.quota_checks import (
    QuotaCheck,
    QuotaContributor,
    QuotaCoverageGap,
    QuotaRecord,
    QuotaReport,
    QuotaRequirement,
    RegionalQuotaAvailability,
    _aggregate_requirements,
    _evaluate_requirement,
    format_quota_report_lines,
)
from nebius_cxcli.terminal_styles import WARNING_COLOR


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
                    "MK8s node-group boot disk size/type is not exposed by current module inputs; "
                    "disk-size quotas were not checked"
                ),
            ),
            QuotaCoverageGap(
                component_id="mk8s",
                instance_id="mk8s",
                component_label="mk8s",
                message=(
                    "MK8s GPU node boot disk size/type is not exposed by current module inputs; "
                    "disk-size quotas were not checked"
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
        "      - MK8s node-group boot disk size/type is not exposed by current module inputs; "
        "disk-size quotas were not checked"
    ) in lines
    assert (
        "      - MK8s GPU node boot disk size/type is not exposed by current module inputs; "
        "disk-size quotas were not checked"
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
                    "MK8s node-group boot disk size/type is not exposed by current module inputs; "
                    "disk-size quotas were not checked"
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
                    "MK8s node-group boot disk size/type is not exposed by current module inputs; "
                    "disk-size quotas were not checked"
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
