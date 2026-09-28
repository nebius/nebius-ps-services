from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from nebius_cxcli import cli
from nebius_cxcli.soperator_install_resume import bound_install_infrastructure_identity
from nebius_cxcli.soperator_release_reconciler import SOPERATOR_RECONCILE_RECEIPT_SCHEMA
from test_soperator_cli_surface import _paths, _slurm_recovery_test_context
from test_soperator_operation import _spec


@pytest.mark.parametrize("changed_values", [False, True])
def test_install_resume_retains_bound_plan_identity_and_rejects_changed_inputs(
    tmp_path, monkeypatch, changed_values
):
    paths = _paths(tmp_path / "project")
    authority, env = _slurm_recovery_test_context(monkeypatch)
    original = asdict(replace(_spec(tmp_path / "spec"), nebius_cluster_id="mk8s-a"))
    first = True
    bound = []
    monkeypatch.setattr(cli, "_soperator_release_refs_for_job_policy", lambda *a, **kw: (object(),))
    monkeypatch.setattr(cli, "_soperator_flux_apply_slurm_job_gate", lambda *a, **kw: ())
    monkeypatch.setattr(cli, "prepare_install_input_repair", lambda **kw: None)

    def apply(*args, **kwargs):
        nonlocal first
        candidate = {**original, "infrastructure_plan_sha256": kwargs["infrastructure_plan_sha256"]}
        if not first and changed_values:
            candidate["desired_values_sha256"] = "sha256:" + "f" * 64
        identity = cli.soperator_sha256(candidate)
        kwargs["bind_operation_spec_sha256"](identity)
        bound.append(identity)
        kwargs["before_reconcile_mutations"]()
        if first:
            first = False
            cli._write_owner_only_json(
                paths.reports_dir / "soperator-release-reconcile-cluster-a-example.json",
                {
                    "schema": SOPERATOR_RECONCILE_RECEIPT_SCHEMA,
                    "operation": {"spec": candidate},
                    "status": "running",
                },
            )
            raise RuntimeError("interrupted release")
        raise RuntimeError("resumed same operation")

    monkeypatch.setattr(cli, "_apply_rendered_flux", apply)
    options = dict(
        command_name="deploy",
        target_ref="cluster-a",
        extra_env=env,
        job_policy="fail",
        cancel_job_ids=(),
        requeue_job_ids=(),
        job_wait_timeout_seconds=60,
        job_refresh_interval_seconds=5,
        assert_authority=lambda: authority,
        operation_started_at=1.0,
        install_recovery=True,
    )
    with pytest.raises(RuntimeError, match="interrupted release"):
        cli._apply_rendered_flux_with_soperator_job_policy(
            {}, paths, infrastructure_plan_sha256=original["infrastructure_plan_sha256"], **options
        )
    expected = (
        "different immutable operation inputs" if changed_values else "resumed same operation"
    )
    with pytest.raises(RuntimeError, match=expected):
        cli._apply_rendered_flux_with_soperator_job_policy(
            {}, paths, infrastructure_plan_sha256="sha256:" + "2" * 64, **options
        )
    assert bound == [cli.soperator_sha256(original)] * (1 if changed_values else 2)


@pytest.mark.parametrize(
    "invalid", ["missing", "duplicate", "cluster", "target", "strategy", "schema", "hash"]
)
def test_install_resume_rejects_unproved_infrastructure_identity(tmp_path, invalid):
    paths = _paths(tmp_path / "project")
    spec = asdict(replace(_spec(tmp_path / "spec"), nebius_cluster_id="mk8s-a"))
    if invalid == "cluster":
        spec["nebius_cluster_id"] = "another-cluster"
    elif invalid == "target":
        spec["target_ref"] = "another-target"
    elif invalid == "strategy":
        spec["strategy"] = "protected-data-plane"
    elif invalid == "hash":
        spec["infrastructure_plan_sha256"] = "not-a-digest"
    receipt = {
        "schema": "unsupported" if invalid == "schema" else SOPERATOR_RECONCILE_RECEIPT_SCHEMA,
        "operation": {"spec": spec},
        "status": "running",
    }
    if invalid != "missing":
        cli._write_owner_only_json(
            paths.reports_dir / "soperator-release-reconcile-a.json", receipt
        )
    if invalid == "duplicate":
        cli._write_owner_only_json(
            paths.reports_dir / "soperator-release-reconcile-b.json", receipt
        )
    with pytest.raises(RuntimeError, match="one exact bound|different immutable inputs"):
        bound_install_infrastructure_identity(
            paths.reports_dir,
            target_ref="cluster-a",
            cluster_id="mk8s-a",
            operation_spec_sha256=cli.soperator_sha256(spec),
        )


@pytest.mark.parametrize("invalid", [None, "strategy", "cluster", "missing"])
def test_deploy_without_strategy_proves_install_before_action_repair(
    tmp_path, monkeypatch, invalid
):
    import copy

    from test_soperator_slurm_journal_repair import journal

    paths = _paths(tmp_path / "project")
    authority, env = _slurm_recovery_test_context(monkeypatch)
    spec = asdict(replace(_spec(tmp_path / "spec"), nebius_cluster_id="mk8s-a"))
    if invalid == "strategy":
        spec["strategy"] = "protected-data-plane"
    elif invalid == "cluster":
        spec["nebius_cluster_id"] = "another-cluster"
    original = journal()
    original.update(
        targetRef="cluster-a",
        operationSpecSha256=cli.soperator_sha256(spec),
        lastCompletedStage="infrastructure-restored",
        policy={
            "jobPolicy": "fail",
            "cancelJobIds": [],
            "requeueJobIds": [],
            "waitTimeoutSeconds": 60,
            "refreshIntervalSeconds": 5,
            "schedulingPause": True,
            "slurmPreimageSha256": cli.soperator_sha256({"mode": "not-required"}),
        },
    )
    cli._write_soperator_slurm_cluster_journal(original)
    cli._write_owner_only_json(
        cli._soperator_slurm_action_journal_path(paths, "cluster-a"), original
    )
    if invalid != "missing":
        cli._write_owner_only_json(
            paths.reports_dir / "soperator-release-reconcile-cluster-a.json",
            {
                "schema": SOPERATOR_RECONCILE_RECEIPT_SCHEMA,
                "operation": {"spec": spec},
                "status": "running",
            },
        )
    monkeypatch.setattr(cli, "_soperator_release_refs_for_job_policy", lambda *a, **kw: (object(),))
    monkeypatch.setattr(cli, "prepare_install_input_repair", lambda **kw: None)
    writes = []
    write = cli._write_soperator_slurm_cluster_journal

    def record(value, **kwargs):
        writes.append(copy.deepcopy(value))
        write(value, **kwargs)

    monkeypatch.setattr(cli, "_write_soperator_slurm_cluster_journal", record)

    def apply(*args, **kwargs):
        assert (
            writes and writes[0]["actionIdentityRepair"]["originalActions"] == original["actions"]
        )
        cli.validate_slurm_recovery_actions(writes[0]["actions"])
        assert kwargs["infrastructure_plan_sha256"] == spec["infrastructure_plan_sha256"]
        raise RuntimeError("reached sealed handoff")

    monkeypatch.setattr(cli, "_apply_rendered_flux", apply)
    expected = "one exact bound|different immutable inputs" if invalid else "reached sealed handoff"
    with pytest.raises(RuntimeError, match=expected):
        cli._apply_rendered_flux_with_soperator_job_policy(
            {},
            paths,
            command_name="deploy",
            target_ref="cluster-a",
            extra_env=env,
            job_policy="fail",
            cancel_job_ids=(),
            requeue_job_ids=(),
            job_wait_timeout_seconds=60,
            job_refresh_interval_seconds=5,
            assert_authority=lambda: authority,
            install_recovery=True,
        )
    if invalid:
        assert writes == []
