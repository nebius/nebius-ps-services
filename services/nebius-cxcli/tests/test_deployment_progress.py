"""Progress and planning cost at the saved-plan / execution boundary."""

from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from nebius_cxcli import cli, deployment_cli
from nebius_cxcli.deployment_plan import DeploymentAction, DeploymentStage, DeploymentStageKind
from nebius_cxcli.deployment_workflow import StageAdmission
from nebius_cxcli.soperator_receipt_io import write_owner_only_json
from test_deployment_campaign import generation, paths


@pytest.mark.parametrize("branch", ["normal", "noop", "release-complete"])
@pytest.mark.parametrize(
    "failure",
    [None, "apply", "validation", "missing-anchor", "authority", "prepare-authority", "seal"],
)
def test_target_seals_reconcile_anchor_before_install_completion(
    tmp_path, monkeypatch, branch, failure
):
    from nebius_cxcli import deployment_target

    events = []
    lease_held = False
    completion_prepared = False
    console = Console(file=StringIO(), force_terminal=False)

    def prepare(*args, **kwargs):
        nonlocal completion_prepared
        completion_prepared = True
        return {"frozen": True}

    monkeypatch.setattr("nebius_cxcli.operation_completion.prepare_completion", prepare)

    class Lease:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            nonlocal lease_held
            lease_held = True
            return self

        def __exit__(self, *args):
            nonlocal lease_held
            lease_held = False
            events.append("released")

        def assert_held(self):
            assert lease_held

    def seal():
        assert lease_held
        events.append("seal")
        if failure == "seal":
            raise RuntimeError("seal failed")

    def apply(*args, **kwargs):
        kwargs["assert_authority"]()
        # Even an observation-only follow-up must retain the admitted plan.
        assert kwargs["infrastructure_plan_sha256"] == "sha256:" + "1" * 64
        events.append("apply")
        if failure == "apply":
            raise RuntimeError("apply failed")
        return None if failure == "missing-anchor" else SimpleNamespace(complete=seal)

    def validate(*args, **kwargs):
        events.append("validation")
        if failure == "validation":
            raise RuntimeError("validation failed")

    def project_authority():
        if failure == "authority" and "validation" in events:
            raise RuntimeError("authority lost")
        if failure == "prepare-authority" and completion_prepared:
            raise RuntimeError("authority lost during completion preparation")

    services = SimpleNamespace(
        progress_console=console,
        console=console,
        GRAFANA_TARGET_CLUSTER_ID_ENV=cli.GRAFANA_TARGET_CLUSTER_ID_ENV,
        GRAFANA_TARGET_KUBE_CONTEXT_ENV=cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV,
        app_mutation_scope=cli.app_mutation_scope,
        _paths_for_target_flux_dir=lambda local, target: local,
        _active_chart_count_for_target=lambda *a, **kw: 1,
        _filter_validations_for_target=lambda *a, **kw: ["post"],
        _pre_app_cluster_smoke_validations=lambda *a: [],
        _pre_soperator_gpu_validations=lambda *a, **kw: [],
        _ordered_post_soperator_validations=lambda *a, **kw: ["post"],
        _prepare_cluster_handoff_kube_env=lambda *a, **kw: {
            cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "id",
            cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "context",
        },
        _non_empty_text=lambda value: value or "",
        _read_kube_system_namespace_uid=lambda **kw: "uid",
        _soperator_release_refs_for_job_policy=lambda *a, **kw: ["soperator"],
        _apply_soperator_install_operation_anchor=lambda **kw: "install-anchor",
        _report_cluster_nodes_status=lambda **kw: None,
        load_soperator_release_snapshot=lambda *a: SimpleNamespace(
            release="4.1.8", capability_contract="upstream-flux-v1"
        ),
        soperator_release_snapshot_path=lambda *a: None,
        resolve_soperator_reconcile_strategy=lambda **kw: None,
        _apply_rendered_flux=apply,
        _apply_rendered_flux_with_soperator_job_policy=apply,
        _collect_grafana_status_after_flux=lambda *a, **kw: [],
        _warn_if_flux_gitops_not_bootstrapped=lambda *a, **kw: None,
        _run_target_deploy_validations=validate,
        _complete_soperator_install_operation_anchor=lambda **kw: events.append("complete"),
    )
    monkeypatch.setattr(deployment_target, "SoperatorOperationLease", Lease)
    monkeypatch.setattr(deployment_target, "prepare_application_runtime", lambda *a, **kw: None)
    monkeypatch.setattr(deployment_target, "apply_ordinary_bundle", lambda *a, **kw: None)
    monkeypatch.setattr("nebius_cxcli.nsight_runtime.collect_nsight_status", lambda *a, **kw: None)

    def run():
        return deployment_target.deploy_application_target(
            services,
            {},
            paths(tmp_path),
            {"target_ref": "cluster"},
            deploy_validations=["post"],
            deployment_lease=SimpleNamespace(
                assert_held=project_authority,
                operation_id="fixture",
                bind_cluster_identity=lambda **kw: None,
            ),
            soperator_plan=SimpleNamespace(
                action=DeploymentAction.NOOP if branch == "noop" else DeploymentAction.RECONCILE,
                source_release="4.1.8",
            ),
            soperator_release_complete=branch == "release-complete",
            soperator_infrastructure_plan_sha256="sha256:" + "1" * 64,
        )

    if failure:
        with pytest.raises(RuntimeError):
            run()
        assert "complete" not in events
        assert ("seal" in events) == (failure == "seal")
    else:
        run()
        assert events == ["apply", "validation", "seal", "complete", "released"]
    assert events[-1] == "released"


def executor(tmp_path):
    payload = {"infra": {"components": []}, "apps": {"charts": []}}
    local = paths(tmp_path)
    manifest = generation(payload).materialize(local)
    return deployment_cli._CliDeploymentExecutor(
        payload, local, manifest, options=deployment_cli.DeployOptions(), lease=SimpleNamespace()
    )


@pytest.mark.parametrize("profile", ["fast-dev-test", "standard"])
@pytest.mark.parametrize("failure", [None, "graph", "desired", "dashboards", "jail", "settings"])
def test_final_observation_checks_graph_once_and_preserves_remaining_gates(
    tmp_path, monkeypatch, profile, failure
):
    from nebius_cxcli import deployment_jail_state, deployment_observation, grafana_cluster

    runner = executor(tmp_path)
    runner.config["apps"]["charts"] = [
        {
            "id": "soperator",
            "enabled": True,
            "target_ref": "cluster",
            "version": "4.1.8",
            "values": {"deploymentProfile": profile},
        }
    ]
    runner.generation = generation(runner.config)
    runner.plan = SimpleNamespace(target_ref="cluster")
    runner._verifying_final = True
    runner._desired_application_generation = lambda: runner.generation
    policy = {"profile": profile}
    runner._readiness_contract = lambda _: policy
    env = {
        cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "cluster-id",
        cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "selected-context",
    }
    events = []

    def check(name, result):
        events.append(name)
        if failure == name:
            raise RuntimeError(f"{name} failed")
        return result

    def graph(paths, **kwargs):
        assert paths == runner.paths
        assert kwargs == {"extra_env": env, "readiness_policy": policy}
        return check("graph", None)

    def desired(*args, **kwargs):
        assert kwargs["generation"] is runner.generation
        assert kwargs["kube_env"] == env
        return check("desired", {"ready": True})

    def dashboards(*args, **kwargs):
        assert kwargs["verify_only"] is True
        return check("dashboards", True)

    monkeypatch.setattr(
        cli, "_resolve_selected_deploy_targets", lambda *a, **kw: [{"target_ref": "cluster"}]
    )
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", lambda *a, **kw: env)
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **kw: "cluster-uid")
    monkeypatch.setattr(cli, "_paths_for_target_flux_dir", lambda *a: runner.paths)
    monkeypatch.setattr(cli, "rendered_soperator_graph_contract", lambda _: {"releases": []})
    monkeypatch.setattr(cli, "wait_for_soperator_release_graph", graph)
    monkeypatch.setattr(
        cli, "_live_soperator_release_for_reconcile", lambda **kw: check("release", "4.1.8")
    )
    monkeypatch.setattr(deployment_observation, "verify_desired_target", desired)
    monkeypatch.setattr(deployment_observation, "rendered_release_identities", lambda *a: {})
    monkeypatch.setattr(
        deployment_observation,
        "observe_release_settings",
        lambda *a, **kw: check("settings", {"release": {"uid": "release-uid"}}),
    )
    monkeypatch.setattr(grafana_cluster, "replay_dashboards", dashboards)
    monkeypatch.setattr(deployment_jail_state, "jail_values", lambda *a: {})
    monkeypatch.setattr(
        deployment_jail_state,
        "observe_jail_storage",
        lambda *a, **kw: check("jail", {"ready": True}),
    )

    expected = ["graph", "desired", "dashboards", "jail", "settings", "release"]
    if failure:
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            runner.observe(None)
        assert events == expected[: expected.index(failure) + 1]
    else:
        assert runner.observe(None) == (
            "4.1.8",
            False,
            {"cluster": {"cluster_id": "cluster-id", "kubernetes_uid": "cluster-uid"}},
        )
        assert events == expected
        assert runner._jail_storage_evidence == {"ready": True}
        assert runner.owned_settings == {"release": {"uid": "release-uid"}}


@pytest.mark.parametrize("applied", [False, True])
@pytest.mark.parametrize("drift", [False, True])
def test_simple_stage_verifies_terraform_only_after_execution(tmp_path, applied, drift):
    runner = executor(tmp_path)
    stage = DeploymentStage(DeploymentStageKind.RECONCILE, runner.config)
    admission = StageAdmission(stage, {}, {"desired": "fixture"})
    calls = []
    observed = {
        "resource_changes": [
            {"address": "terraform_data.example", "change": {"actions": ["update"]}}
        ]
        if drift
        else []
    }

    def plan(**kwargs):
        calls.append(kwargs)
        return None, observed

    runner._terraform_plan = plan
    if applied:
        runner._applied.add(stage.name.value)

    proof = runner.verify(admission)

    assert len(calls) == int(applied)
    assert (proof is not None) == (applied and not drift)


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("failure", [None, RuntimeError, KeyboardInterrupt])
def test_saved_plan_inspection_is_visible_and_cleans_up(tmp_path, monkeypatch, terminal, failure):
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=terminal,
        _environ={"TERM": "xterm"},
        color_system=None,
        width=120,
    )
    monkeypatch.setattr(cli, "progress_console", console)
    runner = executor(tmp_path)
    monkeypatch.setattr(cli, "_config_has_enabled_infra_components", lambda _: True)
    description = "Inspect saved Terraform plan 1: Check deployment infrastructure"

    def plan(directory, *, plan_file, **kwargs):
        # Streamed planning has no competing Rich live surface.
        assert not console._live_stack
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_bytes(b"saved plan")

    def show(directory, **kwargs):
        assert description in output.getvalue()
        assert bool(console._live_stack) == terminal
        if failure:
            raise failure("inspection interrupted")
        return {"resource_changes": []}

    monkeypatch.setattr(cli, "terraform_plan", plan)
    monkeypatch.setattr(cli, "terraform_show_json", show)

    if failure:
        with pytest.raises(failure):
            runner._terraform_plan()
    else:
        _, payload = runner._terraform_plan()
        assert payload["resource_changes"] == []

    assert not console._live_stack
    if not terminal:
        assert "\x1b" not in output.getvalue()
        assert f"START {description}" in output.getvalue()
        assert f"{'FAILED' if failure else 'OK'} {description}" in output.getvalue()
        if failure:
            assert f"OK {description}" not in output.getvalue()


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("failure", [None, RuntimeError, KeyboardInterrupt])
def test_admission_input_checks_keep_progress_visible(tmp_path, monkeypatch, terminal, failure):
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=terminal,
        _environ={"TERM": "xterm"},
        color_system=None,
        width=160,
    )
    monkeypatch.setattr(cli, "progress_console", console)
    runner = executor(tmp_path)
    runner._terraform_plan = lambda **kwargs: (None, {"resource_changes": []})
    stage = DeploymentStage(DeploymentStageKind.RECONCILE, runner.config)
    plan = SimpleNamespace(
        target_ref="cluster", action=DeploymentAction.NOOP, stages=(stage,), target_release="4.1.11"
    )
    description = "Validate frozen Soperator install inputs"

    def preflight(*args):
        assert description in output.getvalue()
        assert bool(console._live_stack) == terminal
        if failure:
            raise failure("input validation stopped")

    monkeypatch.setattr(cli, "_preflight_soperator_install_checks", preflight)
    if failure:
        with pytest.raises(failure):
            runner.admit(plan)
    else:
        assert len(runner.admit(plan)) == 1
    assert not console._live_stack
    if not terminal:
        assert f"{'FAILED' if failure else 'OK'} {description}" in output.getvalue()
        if failure:
            assert f"OK {description}" not in output.getvalue()


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("final", [False, True])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_cluster_observation_uses_one_progress_surface(
    tmp_path, monkeypatch, terminal, final, failure
):
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=terminal,
        _environ={"TERM": "xterm"},
        color_system=None,
        width=160,
    )
    monkeypatch.setattr(cli, "progress_console", console)
    runner = executor(tmp_path)
    runner.generation = generation(runner.config)
    runner._desired_application_generation = lambda **kwargs: runner.generation
    monkeypatch.setattr(cli, "_config_has_enabled_infra_components", lambda _: False)
    monkeypatch.setattr(cli, "_terraform_state_resources", lambda _: [])
    monkeypatch.setattr(deployment_cli, "soperator_target", lambda _: ("cluster", {}))
    monkeypatch.setattr(
        cli,
        "_resolve_selected_deploy_targets",
        lambda *args, **kwargs: [{"target_ref": "cluster", "ownership": "external"}],
    )
    description = "Verify final live cluster state" if final else "Observe Soperator cluster state"

    def handoff(*args, **kwargs):
        assert description in output.getvalue()
        assert len(console._live_stack) == int(terminal)
        assert runner._verifying_final is final
        if final:
            assert "Observe Soperator cluster state" not in output.getvalue()
        raise failure("observation interrupted")

    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", handoff)
    with pytest.raises(failure, match="observation interrupted"):
        if final:
            runner.verify_final(SimpleNamespace(target_ref="cluster"))
        else:
            runner.observe(None)
    assert not runner._verifying_final
    assert not console._live_stack
    if not terminal:
        assert f"FAILED {description}" in output.getvalue()
        assert f"OK {description}" not in output.getvalue()


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("boundary", ["source", "stage", "recovery"])
def test_quiet_admission_renders_show_progress_before_work(
    tmp_path, monkeypatch, terminal, failure, boundary
):
    from nebius_cxcli import deployment_resolution

    output = StringIO()
    console = Console(
        file=output,
        force_terminal=terminal,
        _environ={"TERM": "xterm"},
        color_system=None,
        width=160,
    )
    monkeypatch.setattr(cli, "progress_console", console)
    runner = executor(tmp_path)
    runner.generation = generation(runner.config)
    runner.source_generation = runner.generation
    stage = DeploymentStage(DeploymentStageKind.RECONCILE, runner.config)
    description = {
        "source": "Render observed Soperator source",
        "stage": "Render reconcile admission manifests",
        "recovery": "Validate frozen application inputs before recovery",
    }[boundary]

    def render(*args, **kwargs):
        assert description in output.getvalue()
        assert bool(console._live_stack) == terminal
        raise failure("render stopped")

    if boundary == "source":
        runner.observed_source = runner.config
    if boundary == "recovery":
        runner._application_journal = lambda: SimpleNamespace(
            payload={"selected": ["cluster"]}, entry=lambda _: {}
        )
        monkeypatch.setattr(
            "nebius_cxcli.deployment_plan.soperator_target", lambda _: ("cluster", {})
        )
        monkeypatch.setattr(deployment_resolution, "resolved_application_generation", render)
        action = runner._preflight_install_application_inputs
    else:
        monkeypatch.setattr(cli, "_render_soperator_upgrade_admission", render)

        def action():
            return runner._admit_coordinated_stages(
                SimpleNamespace(target_ref="cluster", stages=(stage,))
            )

    with pytest.raises(failure):
        action()
    assert not console._live_stack
    if not terminal:
        assert f"FAILED {description}" in output.getvalue()
        assert f"OK {description}" not in output.getvalue()


@pytest.mark.parametrize("preflight_values", [{}, {"TF_VAR_fixture_payload": "fixture-value"}])
def test_executor_reuses_preflight_inputs_at_real_artifact_boundary(
    tmp_path, monkeypatch, preflight_values
):
    runner = executor(tmp_path)
    runner.manifest["deploy"] = {"targets": [], "validations": []}
    runner.plan = SimpleNamespace(target_ref="", action=DeploymentAction.INSTALL)
    runner.lease = SimpleNamespace(assert_held=lambda: None, operation_id="fixture")
    runner._bound_identities = lambda: {}
    calls = []

    def preflight(config, paths, **kwargs):
        calls.append("preflight")
        assert calls == ["preflight"], "the same execution repeated its full preflight"
        write_owner_only_json(
            paths.reports_dir / "compatibility-admission.json", {"admitted": True}
        )
        return preflight_values

    monkeypatch.setattr(cli, "_run_deploy_preflight", preflight)
    monkeypatch.setattr(
        cli, "_terraform_runtime_env", lambda _: {"TF_VAR_fixture_runtime": "value"}
    )
    monkeypatch.setattr(cli, "_config_has_enabled_infra_components", lambda _: True)
    monkeypatch.setattr(cli, "_enabled_status_watcher_specs", lambda _: [])
    runner.preflight()
    saved = tmp_path / "saved.tfplan"
    saved.write_bytes(b"saved plan")
    changes = {
        "resource_changes": [
            {"address": "terraform_data.example", "change": {"actions": ["create"]}}
        ]
    }
    runner._terraform_plan = lambda **kwargs: (saved, changes)
    stage = DeploymentStage(DeploymentStageKind.RECONCILE, runner.config)

    class Applied(Exception):
        pass

    def apply(config, paths, **kwargs):
        assert kwargs["extra_env"] == {"TF_VAR_fixture_runtime": "value", **preflight_values}
        assert kwargs["plan_file"] == saved
        assert kwargs["assert_authority"] == runner.lease.assert_held
        raise Applied()

    monkeypatch.setattr(cli, "_run_terraform_apply_with_status", apply)
    with pytest.raises(Applied):
        runner.execute(StageAdmission(stage, changes, {}), recovering=False)
    assert calls == ["preflight"]


def test_postapply_resolution_reuses_initialized_root(tmp_path, monkeypatch):
    from nebius_cxcli import deployment_resolution

    runner = executor(tmp_path)
    runner.generation = generation(runner.config)
    runner.lease = SimpleNamespace(assert_held=lambda: None)
    runner._application_journal = lambda: SimpleNamespace(payload={"selected": []})
    calls = []

    def resolve(cli_module, frozen, paths, *, initialize_terraform=True, target_ref=""):
        calls.append(("resolve", initialize_terraform))
        return frozen

    def publish(cli_module, generation, paths, **kwargs):
        calls.append(("publish", kwargs["selected"]))
        return runner.manifest

    monkeypatch.setattr(deployment_resolution, "resolved_application_generation", resolve)
    monkeypatch.setattr(deployment_resolution, "publish_application_inputs", publish)
    runner._prepare_install_application_inputs()
    assert calls == [("resolve", False), ("publish", [])]


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("failed_step", [None, "resolve", "refresh"])
@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_application_preparation_keeps_progress_visible(
    tmp_path, monkeypatch, terminal, failed_step, error
):
    from nebius_cxcli import deployment_resolution

    output = StringIO()
    console = Console(
        file=output,
        force_terminal=terminal,
        _environ={"TERM": "xterm"},
        color_system=None,
        width=160,
    )
    monkeypatch.setattr(cli, "progress_console", console)
    runner = executor(tmp_path)
    runner.generation = generation(runner.config)
    runner.lease = SimpleNamespace(assert_held=lambda: None)
    runner._application_journal = lambda: SimpleNamespace(payload={"selected": []})
    descriptions = {
        "resolve": "Resolve application inputs for execution",
        "refresh": "Refresh Flux manifests from Terraform outputs",
    }
    calls = []

    def observe(step):
        calls.append(step)
        assert descriptions[step] in output.getvalue()
        assert len(console._live_stack) == int(terminal)
        if failed_step == step:
            raise error("preparation interrupted")

    def resolve(cli_module, frozen, paths, **kwargs):
        assert kwargs == {"initialize_terraform": False, "target_ref": ""}
        observe("resolve")
        return frozen

    def refresh(*args, **kwargs):
        assert kwargs["selected"] == []
        assert kwargs["assert_authority"] == runner.lease.assert_held
        observe("refresh")
        return runner.manifest

    monkeypatch.setattr(deployment_resolution, "resolved_application_generation", resolve)
    monkeypatch.setattr(deployment_resolution, "publish_application_inputs", refresh)
    if failed_step:
        with pytest.raises(error, match="preparation interrupted"):
            runner._prepare_install_application_inputs()
    else:
        runner._prepare_install_application_inputs()
        # Reusing an already resolved generation performs no work or progress.
        before = output.getvalue()
        runner._desired_application_generation()
        assert output.getvalue() == before
    assert calls == (["resolve"] if failed_step == "resolve" else ["resolve", "refresh"])
    assert not console._live_stack
    rendered = output.getvalue()
    if terminal:
        assert "0:00:00" in rendered
    else:
        assert "\x1b" not in rendered
        for step in calls:
            outcome = "FAILED" if step == failed_step else "OK"
            assert f"{outcome} {descriptions[step]}" in rendered
        if failed_step:
            assert f"OK {descriptions[failed_step]}" not in rendered


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("failed_step", [None, "handoff", "uid", "lease", "inputs", "bind"])
@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_target_preparation_progress_covers_handoff_and_authority(
    tmp_path, monkeypatch, terminal, failed_step, error
):
    from nebius_cxcli import deployment_target

    output = StringIO()
    console = Console(
        file=output,
        force_terminal=terminal,
        _environ={"TERM": "xterm"},
        color_system=None,
        width=160,
    )
    monkeypatch.setattr(cli, "progress_console", console)
    descriptions = {
        "handoff": "Connect to target cluster",
        "uid": "Connect to target cluster",
        "lease": "Acquire application authority for cluster",
        "inputs": "Admit application inputs for cluster",
        "bind": "Admit application inputs for cluster",
    }
    calls = []
    monkeypatch.setattr(
        "nebius_cxcli.deployment_app_inventory.assert_live_release_inventory",
        lambda *a, **kw: calls.append("inventory"),
    )

    def observe(step):
        calls.append(step)
        assert descriptions[step] in output.getvalue()
        assert len(console._live_stack) == int(terminal)
        if failed_step == step:
            raise error("handoff interrupted")

    env = {
        cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "id",
        cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "context",
    }

    def handoff(*args, **kwargs):
        observe("handoff")
        assert kwargs["require_renewable_auth"] is True
        assert kwargs["persist_local_kubeconfig"] is False
        assert kwargs["set_current_context"] is False
        return env

    def uid(**kwargs):
        # The authority path independently rechecks the same identity.
        if "uid" not in calls:
            observe("uid")
        return "uid"

    class Lease:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            observe("lease")
            return self

        def __exit__(self, *args):
            calls.append("released")

        def assert_held(self):
            pass

    monkeypatch.setattr(deployment_target, "SoperatorOperationLease", Lease)
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", handoff)
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", uid)
    monkeypatch.setattr(cli, "_paths_for_target_flux_dir", lambda local, target: local)
    monkeypatch.setattr(cli, "_active_chart_count_for_target", lambda *a, **kw: 0)
    monkeypatch.setattr(cli, "_pre_soperator_gpu_validations", lambda *a, **kw: [])
    monkeypatch.setattr(cli, "_ordered_post_soperator_validations", lambda *a, **kw: [])
    monkeypatch.setattr(cli, "_soperator_release_refs_for_job_policy", lambda *a, **kw: ["release"])
    monkeypatch.setattr(cli, "_apply_soperator_install_operation_anchor", lambda **kw: "anchor")

    def complete(**kwargs):
        assert not console._live_stack
        calls.append("complete")

    monkeypatch.setattr(cli, "_complete_soperator_install_operation_anchor", complete)

    def run():
        return deployment_target.deploy_application_target(
            cli,
            {},
            paths(tmp_path),
            {"target_ref": "cluster"},
            deploy_validations=[],
            deployment_lease=SimpleNamespace(
                assert_held=lambda: None,
                operation_id="fixture",
                bind_cluster_identity=lambda **kw: None,
            ),
            on_inputs=lambda *a: observe("inputs"),
            on_identity=lambda *a: observe("bind"),
        )

    if failed_step:
        with pytest.raises(error, match="handoff interrupted"):
            run()
        assert "complete" not in calls
    else:
        assert run()["identity"] == {"cluster_id": "id", "kubernetes_uid": "uid"}
        assert calls == [
            "handoff",
            "uid",
            "lease",
            "inputs",
            "bind",
            "inventory",
            "complete",
            "released",
        ]
    assert not console._live_stack
    if "lease" in calls and failed_step != "lease":
        assert calls[-1] == "released"
    rendered = output.getvalue()
    if terminal:
        assert "0:00:00" in rendered
    else:
        assert "\x1b" not in rendered
        if failed_step:
            assert f"FAILED {descriptions[failed_step]}" in rendered
            assert f"OK {descriptions[failed_step]}" not in rendered


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("failed_step", [None, "observe", "target"])
@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_final_verification_has_progress_and_preserves_errors(
    tmp_path, monkeypatch, terminal, failed_step, error
):
    output = StringIO()
    console = Console(
        file=output,
        force_terminal=terminal,
        _environ={"TERM": "xterm"},
        color_system=None,
        width=160,
    )
    monkeypatch.setattr(cli, "progress_console", console)
    runner = executor(tmp_path)
    runner.generation = generation(runner.config)
    runner._desired_application_generation = lambda **kwargs: runner.generation
    plan = SimpleNamespace(
        target_ref="cluster", target_release="4.1.8", selected_targets=["cluster"]
    )
    descriptions = {
        "observe": "Verify final live cluster state",
        "target": "Verify final acceptance for cluster",
    }
    calls = []

    def step(name, result):
        calls.append(name)
        assert descriptions[name] in output.getvalue()
        assert len(console._live_stack) == int(terminal)
        assert runner._verifying_final is (name == "observe")
        if failed_step == name:
            raise error("verification interrupted")
        return result

    runner.observe = lambda _: step("observe", ("4.1.8", False, {}))
    runner._soperator_target_evidence = lambda *args: step("target", {"ready": True})

    def terraform(**kwargs):
        assert not console._live_stack
        return None, {"resource_changes": []}

    runner._terraform_plan = terraform
    if failed_step:
        with pytest.raises(error, match="verification interrupted"):
            runner.verify_final(plan)
    else:
        assert runner.verify_final(plan)["ready"] is True
    assert not runner._verifying_final
    assert not console._live_stack
    assert calls == (["observe"] if failed_step == "observe" else ["observe", "target"])
    if not terminal:
        for name in calls:
            outcome = "FAILED" if name == failed_step else "OK"
            assert f"{outcome} {descriptions[name]}" in output.getvalue()
        if failed_step:
            assert f"OK {descriptions[failed_step]}" not in output.getvalue()
