"""Lifecycle publication identity survives a date change, never evidence drift."""

import copy
import json
from datetime import date

import pytest

from nebius_cxcli import cli
from nebius_cxcli import compatibility_execution as execution
from nebius_cxcli.compatibility_matrix import assess, digest, load_matrix
from nebius_cxcli.generated_manifest import GENERATED_MANIFEST_SCHEMA
from nebius_cxcli.paths import resolve_project_paths
from nebius_cxcli.project_bundle_transaction import ProjectBundleTransaction
from nebius_cxcli.render import completed_render_generation_matches


def frozen(day):
    material = {
        "schema": "test",
        "config_sha256": digest({}),
        "matrix_sha256": digest(load_matrix()),
        "inventory": [],
        "receipts": [],
        "chart_inputs": {},
        "report": assess([], today=day),
    }
    return {**material, "sha256": digest(material)}


def test_date_only_replay_preserves_exact_evidence_without_modifying_inputs():
    previous, fresh = frozen(date(2026, 9, 25)), frozen(date(2026, 9, 26))
    before = copy.deepcopy((previous, fresh))
    assert execution.preserve_compatibility_observation(fresh, previous) == previous
    assert (previous, fresh) == before


@pytest.mark.parametrize(
    "field", ["schema", "config_sha256", "matrix_sha256", "inventory", "receipts", "chart_inputs"]
)
def test_substantive_evidence_change_is_not_normalized(field):
    previous, fresh = frozen(date(2026, 9, 25)), frozen(date(2026, 9, 26))
    fresh[field] = {"changed": True}
    fresh["sha256"] = digest({k: v for k, v in fresh.items() if k != "sha256"})
    assert execution.preserve_compatibility_observation(fresh, previous) == fresh


def test_expired_support_evidence_is_not_replaced_with_prior_result():
    matrix = load_matrix()
    matrix["sources"]["nvidia-network-25-7"]["expires_on"] = "2026-09-25"
    subject = {
        "component_id": "nvidia-network-operator",
        "instance_id": "cluster",
        "distribution": "nvidia-upstream-network",
        "application_version": "25.7.0",
        "kubernetes_minor": "1.33",
    }
    reports = []
    for day in (25, 26):
        block = frozen(date(2026, 9, day))
        block["report"] = assess([subject], matrix=matrix, today=date(2026, 9, day))
        block["sha256"] = digest({k: v for k, v in block.items() if k != "sha256"})
        reports.append(block)
    previous, fresh = reports
    assert any(row["reason"] == "Affirmative evidence expired" for row in fresh["report"]["rows"])
    assert execution.preserve_compatibility_observation(fresh, previous) == fresh


def test_corrupt_prior_evidence_and_fresh_block_still_fail():
    previous, fresh = frozen(date(2026, 9, 25)), frozen(date(2026, 9, 26))
    previous["sha256"] = "changed"
    with pytest.raises(ValueError, match="integrity"):
        execution.preserve_compatibility_observation(fresh, previous)
    fresh["report"]["rows"] = [{"outcome": "block", "check_id": "test", "reason": "unsupported"}]
    with pytest.raises(ValueError, match="admission failed"):
        execution.preserve_compatibility_observation(fresh, frozen(date(2026, 9, 25)))


def test_explicit_freeze_keeps_fresh_date(monkeypatch):
    monkeypatch.setattr(
        execution, "assess", lambda *a, **kw: assess(*a, **kw, today=date(2026, 9, 26))
    )
    result = execution.freeze_compatibility({}, None, inventory=[])
    assert result["report"]["evaluated_on"] == "2026-09-26"


def test_actual_admission_render_replays_completed_publication_across_midnight(
    tmp_path, monkeypatch
):
    paths = resolve_project_paths(tmp_path / "config.yaml")
    paths.config_path.write_text("{}\n")
    monkeypatch.setattr(cli, "validate_config", lambda *a, **kw: {})
    for name in (
        "_materialize_soperator_component_defaults",
        "_materialize_soperator_render_only_values",
        "materialize_compute_boot_disk_defaults",
        "prune_inactive_mk8s_gpu_app_rows",
        "materialize_mk8s_gpu_app_values",
        "materialize_soperator_child_chart_values",
        "materialize_observability_infra_values",
        "materialize_observability_app_values",
        "materialize_mysterybox_eso_app_values",
        "_raise_on_render_gpu_fabric_drift",
        "render_terraform_artifacts",
        "render_flux",
        "preserve_ordinary_app_generation",
    ):
        monkeypatch.setattr(cli, name, lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_runtime_component_output_values", lambda *a: {})
    day = [25]
    observations = []

    def write_manifest(*args, output_path, **kwargs):
        observations.append(day[0])
        output_path.write_text(
            json.dumps(
                {
                    "schema": GENERATED_MANIFEST_SCHEMA,
                    "execution": {"backend": {}},
                    "runtime_config": {},
                    "render": {"compatibility": frozen(date(2026, 9, day[0]))},
                }
            )
        )

    monkeypatch.setattr(cli, "_write_generated_runtime_manifest", write_manifest)
    args = dict(
        source_payload={}, config_path=paths.config_path, paths=paths, require_soperator_flux=False
    )
    first = cli._render_soperator_upgrade_admission(**args)
    try:
        plan = first.project_generation_plan
        transaction = ProjectBundleTransaction(paths.project_dir)
        transaction.commit(
            plan.writes,
            removals=plan.removals,
            expected_preimages=plan.expected_preimages,
            generation_sha256=plan.sha256,
        )
    finally:
        first.cleanup()
    before = transaction.journal_path.read_bytes()
    day[0] = 26
    resumed = cli._render_soperator_upgrade_admission(**args)
    try:
        assert observations == [25, 26], "fresh compatibility work must still run"
        assert resumed.project_generation_sha256 == plan.sha256
        assert completed_render_generation_matches(
            paths=paths,
            desired=resumed.project_generation_plan,
            admitted_sha256=plan.sha256,
            admitted_preimage_sha256=plan.preimage_sha256,
        )
        assert transaction.journal_path.read_bytes() == before
    finally:
        resumed.cleanup()
