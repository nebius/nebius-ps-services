from __future__ import annotations

import base64
import copy
import json
from contextlib import nullcontext
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import cli, deployment_cli, ordinary_apps, soperator_full_stack_upgrade
from nebius_cxcli.app_mutation import assert_app_mutation_authority, guarded_app_manifest
from nebius_cxcli.deployment_plan import DeploymentStage, DeploymentStageKind, plan_deployment
from nebius_cxcli.deployment_recovery import restore_execution_cache
from nebius_cxcli.deployment_state import DeploymentGeneration, DeploymentState, digest
from nebius_cxcli.deployment_target import deploy_application_target, prepare_application_runtime
from nebius_cxcli.deployment_workflow import StageAdmission, run_deployment
from nebius_cxcli.generated_manifest import runtime_config_from_manifest
from test_deployment_campaign import paths
from test_deployment_plan import config
from test_deployment_state import Store, settings
from test_soperator_full_stack_upgrade import _intent


def bundle(payload):
    targets = [
        {
            "target_ref": ref,
            "instance_id": ref,
            "component_id": "mk8s",
            "ownership": "managed",
            "access": "external",
            "cluster_id_output_name": ref + "_id",
            "component_output_ref": "infra." + ref + ".cluster_id",
            "flux_dir": "flux/targets/" + ref,
        }
        for ref in ("cluster", "other")
    ]
    files = {}
    for ref in ("cluster", "other"):
        doc = {
            "apiVersion": "helm.toolkit.fluxcd.io/v2",
            "kind": "HelmRelease",
            "metadata": {"name": ref, "namespace": "flux-system"},
            "spec": {"values": {"setting": 2}},
        }
        root = "flux/targets/" + ref
        if ref == "other":
            files[root + "/kustomization.yaml"] = base64.b64encode(b"resources: []").decode()
            root += "/ordinary"
        files[root + "/kustomization.yaml"] = base64.b64encode(
            b"resources: [release.yaml]"
        ).decode()
        files[root + "/release.yaml"] = base64.b64encode(yaml.safe_dump(doc).encode()).decode()
    return DeploymentGeneration(
        {
            "schema": "nebius-cxcli-generated/v2",
            "execution": {"backend": {}},
            "render": {"module_sources": []},
            "runtime_config": payload,
            "deploy": {"targets": targets, "validations": []},
        },
        files,
    )


def test_other_target_failure_after_restoration_recovers_without_repeating_upgrade(
    tmp_path, monkeypatch
):
    # This fixture exercises the application journal; transition evaluation has
    # its own real matrix tests and is outside this synthetic campaign adapter.
    monkeypatch.setattr(
        "nebius_cxcli.compatibility_transitions.assess_upgrade_states", lambda *a: None
    )
    source = config()
    desired = copy.deepcopy(source)
    desired["apps"]["charts"][0]["version"] = "1.23.0"
    desired["apps"]["charts"].append(
        {"id": "gateway-helm", "enabled": True, "instance_id": "other"}
    )
    frozen, old = bundle(desired), bundle(source)
    plan = plan_deployment(
        desired, accepted=source, live_release="1.22.3", infrastructure_absent=False
    )
    plan = replace(
        plan,
        stages=(*plan.stages, DeploymentStage(DeploymentStageKind.APPLICATIONS, desired)),
        selected_targets=("cluster", "other"),
    )
    intent = replace(
        _intent(),
        target_ref="cluster",
        source_release="1.22.3",
        target_release="1.23.0",
        cluster_id="cluster-id",
        kubernetes_uid="cluster-uid",
    )
    admissions = tuple(
        StageAdmission(
            stage,
            {"resource_changes": []},
            {
                "generation": frozen.as_payload(),
                "generationId": frozen.identity,
                **(
                    {"targets": ["other"]} if stage.name is DeploymentStageKind.APPLICATIONS else {}
                ),
                **(
                    {
                        "campaign": asdict(intent),
                        "sourceCompatibility": {},
                        "sourceGenerationId": replace(
                            old,
                            manifest={
                                **old.manifest,
                                "render": {**old.manifest["render"], "compatibility": {}},
                            },
                        ).identity,
                    }
                    if index == 0
                    else {}
                ),
            },
        )
        for index, stage in enumerate(plan.stages)
    )
    state = DeploymentState(Store(), settings(), assert_held=lambda: None)
    state.register(
        old,
        evidence={
            "identities": {"cluster": {"cluster_id": "cluster-id", "kubernetes_uid": "cluster-uid"}}
        },
    )
    before = state.read().value["accepted"]
    events, live = [], {}
    restored = False
    fail_once = True

    def campaign(**kwargs):
        nonlocal restored
        if not restored:
            events.extend(["upgrade", "restore-jobs"])
            restored = True
        else:
            events.append("verify-completed-campaign")
        return intent

    monkeypatch.setattr(cli, "_run_soperator_upgrade_campaign", campaign)
    monkeypatch.setattr(
        soperator_full_stack_upgrade,
        "load_campaign_receipt",
        lambda _: SimpleNamespace(status="complete", maintenance="restored"),
    )

    def handoff(config, paths, **kwargs):
        target = kwargs["target"]["target_ref"]
        assert kwargs["persist_local_kubeconfig"] is False
        assert kwargs["set_current_context"] is False
        assert kwargs["require_renewable_auth"] is True
        assert "kube_context" not in kwargs["target"]
        return {
            cli.GRAFANA_TARGET_CLUSTER_ID_ENV: target + "-id",
            cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: target,
            "KUBECONFIG": "temporary",
        }

    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", handoff)
    monkeypatch.setattr(
        cli, "_read_kube_system_namespace_uid", lambda *, kube_context, **kw: kube_context + "-uid"
    )
    for name in (
        "_reconcile_observability_gpu_node_labels",
        "_ensure_mysterybox_eso_runtime_before_flux",
        "_ensure_grafana_runtime_before_flux",
        "_ensure_soperator_notifier_runtime_before_flux",
        "_ensure_soperator_runtime_before_flux",
        "_report_cluster_nodes_status",
    ):
        monkeypatch.setattr(cli, name, lambda *args, **kwargs: assert_app_mutation_authority())
    monkeypatch.setattr(cli, "_collect_grafana_status_after_flux", lambda *args, **kwargs: [])
    monkeypatch.setattr(cli, "_warn_if_flux_gitops_not_bootstrapped", lambda *args, **kwargs: None)

    def apply(target_paths, **kwargs):
        nonlocal fail_once
        if not cli.flux_dir_has_rendered_resources(target_paths.flux_dir):
            return
        assert restored, "ordinary effects require prior Soperator restoration"
        kwargs["assert_authority"]()
        events.append("ordinary-apply")
        if fail_once:
            fail_once = False
            raise RuntimeError("interrupted ordinary write")
        live.update(yaml.safe_load((target_paths.flux_dir / "release.yaml").read_text()))
        live["metadata"].update(uid="release-uid", generation=1)
        live["status"] = {
            "observedGeneration": 1,
            "conditions": [{"type": "Ready", "status": "True"}],
        }

    monkeypatch.setattr(cli, "_apply_rendered_flux", apply)
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_kubectl",
        lambda *a, **kw: SimpleNamespace(stdout=json.dumps(live) if live else ""),
    )

    def executor(root):
        local = paths(root)
        manifest = frozen.materialize(local)
        local.config_path.write_text(yaml.safe_dump(desired))
        active = state.read().value["active"]
        if active:
            restore_execution_cache(local.repo_root, active["recovery"])
            manifest = cli.load_generated_manifest(local.generated_dir)
        runner = deployment_cli._CliDeploymentExecutor(
            runtime_config_from_manifest(manifest),
            local,
            manifest,
            options=deployment_cli.DeployOptions(),
            lease=SimpleNamespace(assert_held=lambda: None),
        )
        runner.plan, runner.generation, runner.source_generation = plan, frozen, old
        runner._desired_application_generation = lambda **kw: frozen
        runner.accepted_evidence = before["evidence"]
        runner.campaign_intent = intent
        runner._campaign_kwargs = lambda: {}
        runner._campaign_jail_authority = lambda: {}
        runner._terraform_plan = lambda **kwargs: (None, {"resource_changes": []})
        runner.admit = lambda _: admissions
        runner._jail_storage_evidence = {"fixture": "storage observed by synthetic campaign"}
        runner.observe = lambda _: (
            "1.23.0",
            False,
            {"cluster": {"cluster_id": "cluster-id", "kubernetes_uid": "cluster-uid"}},
        )
        return runner

    with pytest.raises(RuntimeError, match="interrupted ordinary"):
        run_deployment(
            generation=frozen,
            plan=plan,
            state=state,
            executor=executor(tmp_path / "first"),
            dry_run=False,
            controls={},
        )
    assert restored
    assert state.read().value["accepted"] == before
    assert state.read().value["active"] is not None
    result = run_deployment(
        generation=frozen,
        plan=plan,
        state=state,
        executor=executor(tmp_path / "second"),
        dry_run=False,
        controls={},
    )
    assert events == [
        "upgrade",
        "restore-jobs",
        "ordinary-apply",
        "verify-completed-campaign",
        "ordinary-apply",
    ]
    assert set(result.evidence["targets"]) == {"cluster", "other"}
    assert state.read().value["active"] is None


def test_application_journal_rejects_different_selection_and_identity(tmp_path):
    from nebius_cxcli.deployment_applications import ApplicationJournal

    path = tmp_path / "apps.json"
    journal = ApplicationJournal(path, generation=digest("gen"), selected=("one",))
    identity = {"cluster_id": "id", "kubernetes_uid": "uid"}
    journal.bind("one", identity, digest("bundle"))
    with pytest.raises(RuntimeError, match="selection"):
        ApplicationJournal(path, generation=digest("gen"), selected=("two",))
    with pytest.raises(RuntimeError, match="identity"):
        journal.bind("one", {**identity, "kubernetes_uid": "foreign"}, digest("bundle"))


@pytest.mark.parametrize(
    ("requested", "all_targets", "expected"),
    [
        (None, False, ("cluster", "other")),
        (None, True, ("cluster", "other")),
        ("cluster", False, ("cluster",)),
    ],
)
def test_shared_entrypoint_freezes_selection_and_appends_only_needed_phase(
    tmp_path, monkeypatch, requested, all_targets, expected
):
    from nebius_cxcli.deployment_workflow import DeploymentResult

    source, desired = config(), config()
    desired["apps"]["charts"][0]["version"] = "1.23.0"
    old = bundle(source)
    value = bundle(desired)
    value = DeploymentGeneration(
        {**value.manifest, "execution": {"backend": asdict(settings())}}, value.files
    )
    local = paths(tmp_path)
    manifest = value.materialize(local)
    local.config_path.write_text(yaml.safe_dump(desired))
    store = Store()
    DeploymentState(store, settings(), assert_held=lambda: None).register(
        old, evidence={"identities": {"cluster": {"cluster_id": "id", "kubernetes_uid": "uid"}}}
    )
    monkeypatch.setattr(deployment_cli, "backend_settings_from_config", lambda _: settings())
    monkeypatch.setattr(
        "nebius_cxcli.deployment_local.LocalObjectStore.for_project", lambda _: store
    )
    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", lambda _: None)
    monkeypatch.setattr(deployment_cli._CliDeploymentExecutor, "preflight", lambda _: None)

    def observe(executor, accepted):
        assert accepted is None
        executor.observed_source = source
        return "1.22.3", False, {"cluster": {"cluster_id": "id", "kubernetes_uid": "uid"}}

    monkeypatch.setattr(deployment_cli._CliDeploymentExecutor, "observe", observe)
    monkeypatch.setattr(
        deployment_cli._CliDeploymentExecutor,
        "_terraform_plan",
        lambda _, **kwargs: (None, {"resource_changes": []}),
    )

    def run(**kwargs):
        plan = kwargs["plan"]
        assert plan.selected_targets == expected
        assert (plan.stages[-1].name is DeploymentStageKind.APPLICATIONS) == ("other" in expected)
        return DeploymentResult(True, plan.as_payload(), {})

    monkeypatch.setattr(deployment_cli, "run_deployment", run)
    deployment_cli.deploy_rendered_bundle(
        runtime_config_from_manifest(manifest),
        local,
        manifest,
        options=deployment_cli.DeployOptions(
            target_ref=requested, all_targets=all_targets, dry_run=True
        ),
    )


def test_ordinary_runtime_write_rechecks_parent_fence_inside_helper(tmp_path, monkeypatch):
    from nebius_cxcli.app_mutation import assert_app_mutation_authority

    monkeypatch.setattr(
        "nebius_cxcli.deployment_app_inventory.assert_live_release_inventory", lambda *a, **kw: None
    )
    local = paths(tmp_path)
    value = bundle(
        {
            "infra": {"components": []},
            "apps": {"charts": [{"id": "grafana", "instance_id": "other", "enabled": True}]},
        }
    )
    manifest = value.materialize(local)
    held = True
    writes = []

    def authority():
        if not held:
            raise RuntimeError("lost parent fence")

    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *a, **kw: {
            cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "id",
            cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "ctx",
        },
    )
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **kw: "uid")
    monkeypatch.setattr(cli, "_reconcile_observability_gpu_node_labels", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_ensure_mysterybox_eso_runtime_before_flux", lambda *a, **kw: None)

    def runtime(*a, **kw):
        nonlocal held
        assert_app_mutation_authority()
        writes.append("first")
        held = False
        assert_app_mutation_authority()
        writes.append("unfenced-second")

    monkeypatch.setattr(cli, "_ensure_grafana_runtime_before_flux", runtime)

    def inputs(ref, identity, env, assert_authority):
        assert_authority()
        assert ref == "other" and identity["cluster_id"] == "id"
        writes.append("inputs-admitted")

    with pytest.raises(RuntimeError, match="lost parent fence"):
        deploy_application_target(
            cli,
            runtime_config_from_manifest(manifest),
            local,
            manifest["deploy"]["targets"][1],
            deploy_validations=[],
            deployment_lease=SimpleNamespace(assert_held=authority),
            on_inputs=inputs,
            on_identity=lambda ref, identity: writes.append("identity-bound"),
        )
    assert writes == ["inputs-admitted", "identity-bound", "first"]


@pytest.mark.parametrize("prerequisite_ready", [True, False])
def test_fresh_install_applies_ordinary_prerequisites_before_protected_graph(
    tmp_path, monkeypatch, prerequisite_ready
):
    from nebius_cxcli import deployment_target

    local = paths(tmp_path)
    payload = config()
    manifest = bundle(payload).materialize(local)
    target = manifest["deploy"]["targets"][0]
    target_paths = cli._paths_for_target_flux_dir(local, target)
    ordinary = target_paths.flux_dir / "ordinary"
    ordinary.mkdir()
    (ordinary / "kustomization.yaml").write_text("resources: [prerequisite.yaml]\n")
    (ordinary / "prerequisite.yaml").write_text("kind: HelmRelease\n")
    events = []
    authority = SimpleNamespace(assert_held=lambda: None)
    monkeypatch.setattr(
        deployment_target, "SoperatorOperationLease", lambda **kw: nullcontext(authority)
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *a, **kw: {
            cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "id",
            cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "ctx",
        },
    )
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **kw: "uid")
    monkeypatch.setattr(cli, "_apply_soperator_install_operation_anchor", lambda **kw: "anchor")
    monkeypatch.setattr(cli, "_report_cluster_nodes_status", lambda **kw: None)
    monkeypatch.setattr(
        deployment_target,
        "prepare_application_runtime",
        lambda *a, **kw: events.append("runtime"),
    )

    class StopAtProtectedGraph(RuntimeError):
        pass

    def apply_prerequisite(staged_paths, **kwargs):
        kwargs["assert_authority"]()
        # Exercise the real ordinary staging helper, including its bundle boundary.
        assert (staged_paths.flux_dir / "prerequisite.yaml").is_file()
        assert not (staged_paths.flux_dir / "release.yaml").exists()
        events.append("ordinary")
        if not prerequisite_ready:
            raise RuntimeError("prerequisite not ready")

    def apply_protected(*args, **kwargs):
        kwargs["assert_authority"]()
        events.append("protected")
        raise StopAtProtectedGraph("protected graph reached")

    monkeypatch.setattr(cli, "_apply_rendered_flux", apply_prerequisite)
    monkeypatch.setattr(cli, "_apply_rendered_flux_with_soperator_job_policy", apply_protected)
    plan = plan_deployment(payload, accepted=None, live_release=None, infrastructure_absent=True)
    error = StopAtProtectedGraph if prerequisite_ready else RuntimeError
    message = "protected graph reached" if prerequisite_ready else "prerequisite not ready"
    with pytest.raises(error, match=message):
        deploy_application_target(
            cli,
            runtime_config_from_manifest(manifest),
            local,
            target,
            deploy_validations=[],
            deployment_lease=SimpleNamespace(
                assert_held=lambda: None,
                bind_cluster_identity=lambda **kw: None,
                operation_id="operation",
            ),
            soperator_plan=plan,
        )
    assert events == ["runtime", "ordinary", *(["protected"] if prerequisite_ready else [])]
    assert (ordinary / "prerequisite.yaml").read_text() == "kind: HelmRelease\n"


@pytest.mark.parametrize("lose_authority", [False, True])
def test_soperator_runtime_handoff_uses_real_cli_ownership_guard(
    tmp_path, monkeypatch, lose_authority
):
    monkeypatch.setattr(
        "nebius_cxcli.deployment_app_inventory.assert_live_release_inventory", lambda *a, **kw: None
    )
    held = True
    completed = []
    document = {"kind": "Secret", "metadata": {"name": "grafana"}}
    guarded = []

    def authority():
        if not held:
            raise RuntimeError("lost parent fence")

    def guard(doc):
        guarded.append(doc)
        return {**doc, "owned": True}

    monkeypatch.setattr(ordinary_apps, "runtime_app_documents", lambda *a: [document])
    monkeypatch.setattr(ordinary_apps, "runtime_manifest_guard", lambda *a: guard)
    for name in (
        "_reconcile_observability_gpu_node_labels",
        "_ensure_mysterybox_eso_runtime_before_flux",
    ):
        monkeypatch.setattr(cli, name, lambda *a, **kw: assert_app_mutation_authority())

    def grafana(*a, **kw):
        nonlocal held
        assert guarded_app_manifest(document) == {**document, "owned": True}
        completed.append("grafana")
        if lose_authority:
            held = False
            guarded_app_manifest(document)
            pytest.fail("A runtime write passed after losing the parent fence")

    def notifier(*a, **kw):
        assert_app_mutation_authority()
        completed.append("notifier")

    def soperator(*a, **kw):
        assert_app_mutation_authority()
        completed.append("soperator")

    monkeypatch.setattr(cli, "_ensure_grafana_runtime_before_flux", grafana)
    monkeypatch.setattr(cli, "_ensure_soperator_notifier_runtime_before_flux", notifier)
    monkeypatch.setattr(cli, "_ensure_soperator_runtime_before_flux", soperator)
    monkeypatch.setattr(cli, "_mysterybox_eso_rendered_secret_keys", lambda *a: set())

    def run():
        prepare_application_runtime(
            cli,
            {"client_info": {}},
            paths(tmp_path),
            target_ref="cluster",
            kube_env={},
            assert_authority=authority,
            soperator_owned=True,
        )

    if lose_authority:
        with pytest.raises(RuntimeError, match="lost parent fence"):
            run()
        assert completed == ["grafana"]
    else:
        run()
        assert completed == ["grafana", "notifier", "soperator"]
    assert guarded == [document, document]
    assert guarded_app_manifest(document) == document


def test_removing_application_resources_fails_before_deployment_admission(tmp_path):
    old = bundle(config())
    files = dict(old.files)
    files["flux/targets/other/ordinary/kustomization.yaml"] = base64.b64encode(
        b"resources: []"
    ).decode()
    desired = DeploymentGeneration(old.manifest, files)
    runner = deployment_cli._CliDeploymentExecutor(
        None, paths(tmp_path), {}, options=deployment_cli.DeployOptions(), lease=None
    )
    runner.plan = SimpleNamespace(selected_targets=("other",))
    runner.generation = desired
    runner.target_generations = {"other": old}
    with pytest.raises(RuntimeError, match="removal or renaming"):
        runner.assert_supported_application_inventory()


@pytest.mark.parametrize("mismatch", ["uid", "values"])
def test_completed_campaign_must_reprove_identity_and_settings_before_other_targets(
    tmp_path, monkeypatch, mismatch
):
    frozen = bundle(config())
    local = paths(tmp_path)
    manifest = frozen.materialize(local)
    runner = deployment_cli._CliDeploymentExecutor(
        None, local, manifest, options=deployment_cli.DeployOptions(), lease=None
    )
    runner.generation = frozen
    runner.plan = SimpleNamespace(target_ref="cluster", target_release="1.23.0")
    runner.campaign_intent = SimpleNamespace(cluster_id="cluster-id", kubernetes_uid="cluster-uid")
    runner._campaign_kwargs = lambda: {}
    monkeypatch.setattr(cli, "_run_soperator_upgrade_campaign", lambda **kw: runner.campaign_intent)
    monkeypatch.setattr(
        soperator_full_stack_upgrade,
        "load_campaign_receipt",
        lambda _: SimpleNamespace(status="complete", maintenance="restored"),
    )

    def observe(_):
        assert runner._verifying_final
        if mismatch == "values":
            raise RuntimeError("Desired resource settings have not converged")
        return (
            "1.23.0",
            False,
            {"cluster": {"cluster_id": "cluster-id", "kubernetes_uid": "different"}},
        )

    runner.observe = observe
    with pytest.raises(
        RuntimeError, match="settings have not converged|identity or desired release"
    ):
        runner._execute_coordinated_stage(SimpleNamespace(), recovering=True)
    assert runner._campaign_complete is False
