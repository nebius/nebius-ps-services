"""The parent owns ordinary smoke after restoring its scheduling barrier."""

import ast
import inspect
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli
from nebius_cxcli.soperator_full_stack_upgrade import run_campaign
from test_soperator_full_stack_upgrade import _intent


def closure(name, environment):
    environment["__name__"] = "nebius_cxcli.soperator_campaign_cli"
    environment["__package__"] = "nebius_cxcli"
    tree = ast.parse(inspect.getsource(cli._run_soperator_upgrade_campaign))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, "<production-fast-campaign>", "exec"), environment)
    return environment[name]


@pytest.mark.parametrize("interrupted", [False, True])
def test_campaign_smoke_follows_scheduling_and_resumes_without_replaying_segments(
    tmp_path, monkeypatch, interrupted
):
    events = []
    reservations = {"owned"}
    intent = _intent()
    ready = False
    smoke_attempts = 0
    monkeypatch.setattr(cli, "_soperator_maintenance_pause_records", lambda _: ())
    monkeypatch.setattr(cli, "applied_slurm_held_job_records", lambda _: ())
    monkeypatch.setattr(cli, "_soperator_upgrade_reservation_names", lambda **kw: reservations)
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_delete_maintenance_reservation",
        lambda **kw: reservations.discard(kw["reservation_name"]),
    )

    def open_admission():
        nonlocal ready
        assert not reservations
        ready = True
        events.append("ordinary-admission")

    def smoke():
        nonlocal smoke_attempts
        assert ready and not reservations
        assert events[-1] == "desired-proof"
        smoke_attempts += 1
        if interrupted and smoke_attempts == 1:
            raise RuntimeError("smoke transport interrupted")
        events.append("smoke-passed")
        return {"status": "passed", "generation": intent.digest}

    checks = SimpleNamespace(
        verify_handoff=lambda: None,
        authorize_admission=lambda: None,
        verify_barrier=lambda: None,
        finish_admission=open_admission,
        documents_projection=lambda: {},
    )
    environment = {
        "cli": cli,
        "namespace": "soperator",
        "intent": intent,
        "kube_context": "explicit-target",
        "kube_env": {},
        "_assert_campaign_authority": lambda: None,
        "_campaign_checks": lambda: checks,
        "_fast_campaign_smoke": smoke,
        "deployment_hooks": SimpleNamespace(
            verify_desired=lambda **kw: events.append("desired-proof")
        ),
    }
    restore = closure("_restore_maintenance", environment)

    def segment():
        events.append("segment")
        return cli.CampaignSegmentResult(evidence={})

    def run():
        return run_campaign(
            path=tmp_path / "campaign.json",
            intent=intent,
            segment_executors={name: segment for name in intent.segments},
            enter_maintenance=lambda *_: {"namespace": "soperator", "reservationName": "owned"},
            restore_maintenance=restore,
            assert_fence=lambda: None,
        )

    if interrupted:
        with pytest.raises(RuntimeError, match="smoke transport interrupted"):
            run()
        assert cli.load_campaign_receipt(tmp_path / "campaign.json").maintenance == "restoring"
    result = run()
    assert result.status == "complete"
    assert events.count("segment") == len(intent.segments)
    assert events[-3:] == ["ordinary-admission", "desired-proof", "smoke-passed"]
    assert result.maintenance_evidence["restoration"]["fastSlurmReadiness"] == {
        "status": "passed",
        "generation": intent.digest,
    }


@pytest.mark.parametrize("fast", [False, True])
@pytest.mark.parametrize("verify_only", [False, True])
def test_parent_smoke_uses_frozen_target_and_parent_generation(
    tmp_path, monkeypatch, fast, verify_only
):
    from nebius_cxcli import soperator_fast_readiness
    from nebius_cxcli.soperator_deployment_profile import apply_deployment_profile

    values = {
        "soperatorActiveChecks": {"overrideValues": {}},
        "slurmCluster": {"overrideValues": {}},
    }
    if fast:
        apply_deployment_profile(values, "fast-dev-test")
    intent = _intent()
    target = object()
    target_paths = SimpleNamespace(flux_dir=tmp_path / "frozen")
    calls = []

    def paths_for(paths, selected):
        assert selected is target
        return target_paths

    def rendered(path):
        assert path == target_paths.flux_dir
        return values

    def verify(**kwargs):
        calls.append(kwargs)
        return {"status": "passed"}

    monkeypatch.setattr(cli, "_paths_for_target_flux_dir", paths_for)
    monkeypatch.setattr(cli, "_rendered_soperator_upstream_values", rendered)
    monkeypatch.setattr(soperator_fast_readiness, "verify_fast_readiness", verify)
    policy = object()
    environment = {
        "cli": cli,
        "paths": SimpleNamespace(reports_dir=tmp_path),
        "selected_target": target,
        "intent": intent,
        "namespace": "soperator",
        "kube_context": "explicit-target",
        "kube_env": {},
        "_assert_campaign_authority": lambda: None,
        "_campaign_checks": lambda: SimpleNamespace(load_target=lambda: (policy, (), ())),
    }
    result = closure("_fast_campaign_smoke", environment)(verify_only=verify_only)
    if fast:
        assert result == {"status": "passed"}
        assert calls[0]["generation"] == intent.digest
        assert calls[0]["reports_dir"] == tmp_path
        assert calls[0]["policy"] is policy
        assert calls[0]["verify_only"] is verify_only
        assert calls[0]["confirm_retirement"] is cli._confirm_explicit_action
    else:
        assert result == {"status": "not-applicable"}
        assert not calls
