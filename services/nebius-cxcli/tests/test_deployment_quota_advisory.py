from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli
from nebius_cxcli import quota_checks as quota
from nebius_cxcli.quota_checks import QuotaCheck, QuotaContributor, QuotaReport


def gpu(*, required=8, available=0, tenant_limit=32, project_limit=None, **kwargs):
    return QuotaCheck(
        component_id="mk8s",
        instance_id="cluster",
        component_label="cluster",
        quota_name="compute.instance.gpu.h100",
        region="region",
        required=required,
        reason="GPU workers",
        unit="count",
        available=available,
        sufficient=None if available is None else required <= available,
        tenant_limit=tenant_limit,
        tenant_usage=kwargs.get("tenant_usage", 0),
        project_limit=project_limit,
        project_usage=kwargs.get("project_usage", 0),
        source_scope="capacity-dashboard/on-demand",
        description="Physical capacity",
        contributors=(QuotaContributor("mk8s", "cluster", "cluster", required, "GPU workers"),),
    )


def run_gate(monkeypatch, tmp_path, checks, *, phase="deploy", managed=()):
    report = QuotaReport(
        tenant_id="tenant",
        project_id="project",
        region_id="region",
        checked_at="now",
        checks=checks,
    )
    monkeypatch.setattr(cli, "terraform_init", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_assess_live_quota_report", lambda *a, **kw: report)
    monkeypatch.setattr(
        cli, "_managed_mk8s_quota_requirements_from_terraform_state", lambda *a, **kw: managed
    )
    paths = SimpleNamespace(infra_dir=tmp_path, config_path=tmp_path / "config.yaml")
    return cli._raise_on_generated_bundle_live_quota_issues(
        "config", paths, manifest={}, runtime_env={}, phase=phase
    )


@pytest.mark.parametrize("available", [0, 4, None])
def test_deploy_continues_with_pending_or_unknown_physical_capacity(
    monkeypatch, tmp_path, capsys, available
):
    report = run_gate(monkeypatch, tmp_path, (gpu(available=available),))
    assert report.checks[0].available == available
    if available is not None:
        assert "capacity is advisory" in capsys.readouterr().out


@pytest.mark.parametrize(
    "settings",
    [
        {"tenant_limit": 4},
        {"project_limit": 4},
        {"tenant_limit": 8, "tenant_usage": 1},
        {"project_limit": 8, "project_usage": 1},
    ],
)
@pytest.mark.parametrize("available", [0, 64, None])
def test_actual_gpu_quota_shortfall_blocks_regardless_of_capacity(
    monkeypatch, tmp_path, settings, available
):
    with pytest.raises(RuntimeError, match="quota allowance is insufficient for deploy"):
        run_gate(monkeypatch, tmp_path, (gpu(available=available, **settings),))


def test_gpu_shapes_share_one_aggregate_quota(monkeypatch, tmp_path):
    a = gpu(required=8, tenant_limit=12)
    b = replace(a, instance_id="other", component_label="other")
    with pytest.raises(RuntimeError, match="requires 16, available 12"):
        run_gate(monkeypatch, tmp_path, (a, b))


def test_unknown_allowance_does_not_turn_physical_shortage_into_quota_failure(
    monkeypatch, tmp_path
):
    assert run_gate(monkeypatch, tmp_path, (gpu(tenant_limit=None),))


def test_non_gpu_quota_still_blocks(monkeypatch, tmp_path):
    check = replace(gpu(tenant_limit=4), quota_name="compute.instance.vcpu", source_scope="tenant")
    with pytest.raises(RuntimeError, match="insufficient for deploy"):
        run_gate(monkeypatch, tmp_path, (check,))


def test_explicit_generated_validation_keeps_capacity_diagnostics(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="insufficient for generated bundle validation"):
        run_gate(monkeypatch, tmp_path, (gpu(),), phase="generated bundle validation")


def test_managed_state_discount_precedes_allowance_decision(monkeypatch, tmp_path):
    check = gpu(required=16, tenant_limit=16, tenant_usage=8)
    requirement = cli.QuotaRequirement(
        component_id="mk8s",
        instance_id="cluster",
        component_label="cluster",
        quota_name=check.quota_name,
        region="region",
        required=8,
        reason="managed workers",
    )
    report = run_gate(monkeypatch, tmp_path, (check,), managed=(requirement,))
    assert report.checks[0].required == 8


def test_failed_capacity_fetch_does_not_hide_real_quota_shortfall(monkeypatch, tmp_path):
    requirement = quota.AggregatedQuotaRequirement(
        component_id="mk8s",
        instance_id="cluster",
        component_label="cluster",
        quota_name="compute.instance.gpu.h100",
        region="region",
        required=8,
        reason="GPU workers",
        gpu_capacity_shape=quota.GpuCapacityShape(
            platform="gpu-h100",
            preset="8gpu",
            fabric="fabric",
            mode="regular",
            gpu_count_per_instance=8,
        ),
    )
    record = quota.QuotaRecord(
        name=requirement.quota_name,
        region="region",
        limit=4,
        usage=0,
        service="compute",
        description="GPUs",
        unit="count",
        state="ACTIVE",
        usage_state="ACTIVE",
        usage_percentage="0",
    )
    check = quota._evaluate_requirement(
        requirement,
        tenant_quotas={(record.name, record.region): record},
        project_quotas={},
        capacity_resource_advice=None,
    )
    assert check.source_scope == "unresolved"
    with pytest.raises(RuntimeError, match="quota allowance is insufficient"):
        run_gate(monkeypatch, tmp_path, (check,))
