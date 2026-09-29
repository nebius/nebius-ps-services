"""Shared adapter executes frozen bundles and recovers across private runner roots."""

import base64
import copy
import json
import subprocess
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import cli, deployment_cli
from nebius_cxcli.deployment_plan import DeploymentAction
from nebius_cxcli.deployment_state import DeploymentGeneration, DeploymentState
from nebius_cxcli.generated_manifest import runtime_config_from_manifest
from test_deployment_campaign import generation, paths
from test_deployment_plan import config
from test_deployment_state import Store, settings


@pytest.mark.parametrize("soperator", [False, True])
@pytest.mark.parametrize("preview", [False, True])
def test_adapter_uses_rendered_configuration_for_ordinary_and_soperator_projects(
    tmp_path, monkeypatch, soperator, preview
):
    _exercise_adapter(tmp_path, monkeypatch, soperator=soperator, preview=preview, interrupt=False)


def test_adapter_recovers_partial_creation_without_reclassifying_it_as_unowned(
    tmp_path, monkeypatch
):
    _exercise_adapter(tmp_path, monkeypatch, soperator=True, preview=False, interrupt=True)


def test_render_publication_before_capture_cannot_mix_old_manifest_and_new_files(
    tmp_path, monkeypatch
):
    _exercise_adapter(
        tmp_path, monkeypatch, soperator=True, preview=False, interrupt=False, rerender=True
    )


def test_recovery_admits_the_same_effective_jail_inputs_as_initial_execution(tmp_path, monkeypatch):
    _exercise_adapter(
        tmp_path, monkeypatch, soperator=True, preview=False, interrupt=True, hydrated=True
    )


def test_adapter_bootstraps_backend_once_before_acquiring_real_execution_lease(
    tmp_path, monkeypatch
):
    _exercise_adapter(
        tmp_path, monkeypatch, soperator=False, preview=False, interrupt=False, real_lease=True
    )


def _exercise_adapter(
    tmp_path,
    monkeypatch,
    *,
    soperator,
    preview,
    interrupt,
    target_ref=None,
    real_lease=False,
    late_failure=None,
    publication_failure=False,
    footer_failure=False,
    hydrated=False,
    execution_profile=None,
    rerender=False,
):
    payload = config() if soperator else {"infra": {"components": []}, "apps": {"charts": []}}
    execution_payload = copy.deepcopy(payload)
    if hydrated:
        if execution_profile is not None:
            execution_payload["apps"]["charts"][0]["values"]["deploymentProfile"] = (
                execution_profile
            )
        execution_payload["apps"]["charts"][0]["values"]["settings"]["limit"] = 2
    original = paths(tmp_path / "workspace")
    # The manifest is rendered once. A later source edit cannot change its inputs.
    frozen = generation(payload)
    frozen = DeploymentGeneration(
        {
            **frozen.manifest,
            "execution": {"backend": asdict(settings())},
            "render": {"module_sources": []},
        },
        {
            **frozen.files,
            "flux/values.yaml": base64.b64encode(b"phase: rendered\n").decode(),
        },
    )
    manifest = frozen.materialize(original)
    runtime = runtime_config_from_manifest(manifest)
    caller_manifest = manifest
    if rerender:
        # The caller resolved A, then another render publishes B before deploy's
        # capture. The execution and its attempt identity must both consume B.
        payload["apps"]["charts"][0]["values"]["settings"]["limit"] = 7
        frozen = DeploymentGeneration({**frozen.manifest, "runtime_config": payload}, frozen.files)
        publication_lock = deployment_cli.render_publication_lock

        @contextmanager
        def publish_before_capture(**kwargs):
            nonlocal manifest
            with publication_lock(**kwargs):
                from nebius_cxcli.generated_manifest import manifest_path_for_generated_dir

                manifest = {**caller_manifest, "runtime_config": payload}
                manifest_path_for_generated_dir(original.generated_dir).write_text(
                    json.dumps(manifest)
                )
                yield

        monkeypatch.setattr(deployment_cli, "render_publication_lock", publish_before_capture)
    source_edit = b"unrendered: edit\n"
    original.config_path.write_bytes(source_edit)
    store = Store()
    effects = []
    runner_roots = []
    planned = []
    created = False
    fail_once = interrupt
    observed = []
    identities = (
        {"cluster": {"cluster_id": "cluster-id", "kubernetes_uid": "uid"}} if soperator else {}
    )

    monkeypatch.setattr(deployment_cli, "backend_settings_from_config", lambda _: settings())
    monkeypatch.setattr(
        "nebius_cxcli.deployment_local.LocalObjectStore.for_project", lambda _: store
    )
    bootstrap_calls = []
    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", lambda _: bootstrap_calls.append(1))

    def preflight(_config, current_paths, **kwargs):
        # The orchestration adapter consumes the receipt produced by its peer.
        # Native compatibility admission is exercised in test_compatibility_*.
        from nebius_cxcli.soperator_receipt_io import write_owner_only_json

        assert (current_paths.flux_dir / "values.yaml").read_text() == (
            "phase: hydrated\n" if interrupt and len(effects) == 1 else "phase: rendered\n"
        )
        write_owner_only_json(
            current_paths.reports_dir / "compatibility-admission.json",
            {
                "admitted": True,
                "rows": [
                    {
                        "axis": "constraints",
                        "check_id": "frozen_soperator_release_contract",
                        "subject_sha256": "fixture-target",
                        "evidence": copy.deepcopy(_config),
                    }
                ]
                if hydrated
                else [],
                "matrix_sha256": "fixture-matrix",
            },
        )
        return {}

    def authored_compatibility(config, replay_paths, frozen, **kwargs):
        assert config == payload
        assert (replay_paths.flux_dir / "values.yaml").read_text() == "phase: rendered\n"
        return {
            "admitted": True,
            "matrix_sha256": "fixture-matrix",
            "rows": [
                {
                    "axis": "constraints",
                    "check_id": "frozen_soperator_release_contract",
                    "subject_sha256": "fixture-target",
                    "evidence": copy.deepcopy(config),
                }
            ]
            if hydrated
            else [],
        }

    monkeypatch.setattr(cli, "admit_compatibility", authored_compatibility)
    monkeypatch.setattr(
        deployment_cli, "use_generation_soperator_releases", lambda *a, **kw: nullcontext({})
    )
    monkeypatch.setattr(cli, "_run_deploy_preflight", preflight)
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _: {})
    monkeypatch.setattr(cli, "terraform_init", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "terraform_validate", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "preflight_soperator_backup_inputs", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "preflight_soperator_sssd_inputs", lambda *a, **kw: {})
    monkeypatch.setattr(cli, "_preflight_soperator_install_checks", lambda *a: None)
    monkeypatch.setattr(cli, "accept_ordinary_app_baseline", lambda *a, **kw: None)

    @contextmanager
    def lease(**kwargs):
        held = SimpleNamespace(assert_held=lambda: None, operation_id=kwargs["operation_id"])
        if not real_lease:
            yield held
            return
        from pathlib import Path

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with deployment_cli.deployment_execution(**kwargs) as acquired:
            assert len(bootstrap_calls) == 1
            yield acquired

    monkeypatch.setattr(cli, "_deployment_execution", lease)

    def observe(executor, accepted):
        observed.append(accepted)
        if executor._verifying_final:
            executor.owned_settings = {"flux-system/soperator": {"uid": "release-uid"}}
        if soperator and created:
            executor.observed_source = copy.deepcopy(payload)
        return ("1.22.3" if soperator and created else None, not created, identities)

    def terraform_plan(executor, **kwargs):
        # Simulate a partial provider creation whose successful effect outlives the runner.
        if soperator and not created:
            return None, {
                "resource_changes": [
                    {
                        "address": "module.cluster.nebius_mk8s_v1_cluster.this",
                        "type": "nebius_mk8s_v1_cluster",
                        "change": {
                            "actions": ["create"],
                            "after": {"name": "cluster"},
                            "after_unknown": {},
                        },
                    }
                ]
            }
        return None, {"resource_changes": []}

    def execute(config, private_paths, generated, **kwargs):
        nonlocal created, fail_once
        assert kwargs["deployment_lease"] is not None
        expected = execution_payload if hydrated and effects else payload
        assert copy.deepcopy(dict(config)) == expected
        assert yaml.safe_load(private_paths.config_path.read_text()) == expected
        assert generated["runtime_config"] == expected
        assert private_paths.project_dir != original.project_dir
        if interrupt and len(effects) == 1:
            assert (private_paths.flux_dir / "values.yaml").read_text() == "phase: hydrated\n"
        runner_roots.append(private_paths.repo_root)
        effects.append("create" if not created else "reconcile")
        planned.append(kwargs["soperator_plan"].action)
        created = True
        if fail_once:
            fail_once = False
            from nebius_cxcli.deployment_recovery import checkpoint_execution
            from nebius_cxcli.soperator_receipt_io import write_owner_only_json

            (private_paths.flux_dir / "values.yaml").write_text("phase: hydrated\n")
            write_owner_only_json(
                private_paths.reports_dir / "compatibility-admission.json",
                {"admitted": False, "matrix_sha256": "stale-cache"},
            )
            from nebius_cxcli.generated_manifest import manifest_path_for_generated_dir

            # Model publication of effective bytes and their metadata together.
            # This adapter fixture has no real target/application journal; that
            # authenticated repair boundary has dedicated publication tests.
            current = {
                **generated,
                "render": {**generated.get("render", {}), "compatibility": {"phase": "hydrated"}},
                "runtime_config": execution_payload if hydrated else payload,
            }
            manifest_path_for_generated_dir(private_paths.generated_dir).write_text(
                json.dumps(current)
            )
            if hydrated:
                private_paths.config_path.write_text(yaml.safe_dump(execution_payload))
            checkpoint_execution()
            raise RuntimeError("lost provider acknowledgement")
        if late_failure:
            from nebius_cxcli.deploy_validation_report import build_deploy_validation_report
            from nebius_cxcli.deployment_reports import publish_deployment_reports

            specs = [{"kind": "soperator_cluster_smoke", "report_file": "validation.json"}]
            (private_paths.reports_dir / "validation.json").write_text(
                json.dumps({"passed": True, "summary": "Native checks passed."})
            )
            (private_paths.reports_dir / "deploy-report.md").write_text("Validation passed\n")
            report = build_deploy_validation_report(specs, reports_dir=private_paths.reports_dir)
            report = publish_deployment_reports(private_paths, original, specs, report)
            return cli.DeployRunSummary(validation_report=report, cluster_identities=identities)
        return SimpleNamespace(cluster_identities=identities)

    monkeypatch.setattr(
        deployment_cli._CliDeploymentExecutor,
        "_desired_application_generation",
        lambda self, **kw: self.generation,
    )
    monkeypatch.setattr(deployment_cli._CliDeploymentExecutor, "observe", observe)
    monkeypatch.setattr(deployment_cli._CliDeploymentExecutor, "_terraform_plan", terraform_plan)
    monkeypatch.setattr(cli, "_deploy_generated_artifacts", execute)
    if target_ref:
        monkeypatch.setattr(
            cli, "_resolve_selected_deploy_targets", lambda *a, **kw: [{"target_ref": target_ref}]
        )
    options = deployment_cli.DeployOptions(dry_run=preview, target_ref=target_ref)

    def attempt_state():
        from nebius_cxcli.deployment_state import digest
        from nebius_cxcli.deployment_workflow import _semantic_controls

        attempt = digest(
            {"generation": frozen.identity, "controls": _semantic_controls(options.controls())}
        )[7:]
        return DeploymentState(store, settings(), assert_held=lambda: None, attempt=attempt)

    def run():
        return deployment_cli.deploy_rendered_bundle(
            runtime, original, caller_manifest, options=options
        )

    if late_failure:
        from nebius_cxcli import deployment_reports

        original_error = RuntimeError("PRIVATE-FAILURE-SENTINEL")

        def fail(*args, **kwargs):
            if footer_failure:

                def reject_footer(*args, **kwargs):
                    raise OSError("footer output rejected")

                monkeypatch.setattr(cli, "_print_deploy_command_footer", reject_footer)
            if publication_failure:

                def reject_replace(*args, **kwargs):
                    raise OSError("report publication rejected")

                monkeypatch.setattr(deployment_reports.os, "replace", reject_replace)
            raise original_error

        if late_failure == "verify_final":
            monkeypatch.setattr(deployment_cli._CliDeploymentExecutor, "verify_final", fail)
        else:
            monkeypatch.setattr(DeploymentState, "accept", fail)
        with pytest.raises(RuntimeError) as caught:
            run()
        assert caught.value is original_error
        assert runner_roots and all(not root.exists() for root in runner_roots)
        record = attempt_state().read().value
        assert record["active"] is not None and record["accepted"] is None
        if not publication_failure:
            saved = json.loads((original.reports_dir / "deploy-report.json").read_text())
            assert saved["status"] == "failed"
            assert saved["execution_failure"] == {"failed": True}
            assert saved["results"][0]["status"] == "passed"
            assert "Execution failure" in (original.reports_dir / "deploy-report.md").read_text()
        for name in ["deploy-report.json", "deploy-report.md"]:
            output = original.reports_dir / name
            assert output.stat().st_mode & 0o777 == 0o600
            assert "SENTINEL" not in output.read_text()
            assert "cxcli-deploy-" not in output.read_text()
        return
    if target_ref:
        with pytest.raises(RuntimeError, match="Rendered Soperator changes require"):
            run()
        assert not effects and not store.objects
        assert original.config_path.read_bytes() == source_edit
        return
    if interrupt:
        with pytest.raises(RuntimeError, match="lost provider acknowledgement"):
            run()
        state = attempt_state().read()
        assert state.value["active"]["recovery"]
        assert state.value["accepted"] is None
    result = run()
    assert original.config_path.read_bytes() == source_edit
    assert DeploymentGeneration.capture(original, manifest).identity == frozen.identity
    if preview:
        assert result is None and not effects and not store.objects
    else:
        accepted = attempt_state().read()
        assert accepted.value["active"] is None
        assert accepted.value["accepted"]["evidence"]["identities"] == identities
        assert result.cluster_identities == identities
        if interrupt:
            assert effects == ["create", "reconcile"]
            assert runner_roots[0] != runner_roots[1]
            # Admission is not repeated against the half-created deployment.
            assert len(observed) == 2  # initial classification and final independent observation
        if soperator and not hydrated:
            run()
            assert planned[-1] is DeploymentAction.NOOP


@pytest.mark.parametrize("interrupt", [False, True])
def test_grafana_handoff_runs_after_acceptance_on_initial_resumed_and_unchanged_runs(
    tmp_path, monkeypatch, interrupt
):
    from nebius_cxcli import grafana_access

    calls = []

    def handoff(config, source, destination, summary, targets, validations):
        assert source.project_dir.exists()
        assert source != destination
        assert summary.cluster_identities
        calls.append((tuple(targets), dict(summary.cluster_identities)))
        return summary

    monkeypatch.setattr(grafana_access, "complete_grafana_handoff", handoff)
    # The fixture executes installation (optionally interrupted/resumed), then
    # a new unchanged deployment. Failed attempts never reach the handoff.
    _exercise_adapter(tmp_path, monkeypatch, soperator=True, preview=False, interrupt=interrupt)
    assert len(calls) == 2
    assert calls[0] == calls[1]


def test_grafana_handoff_never_runs_for_deployment_preview(tmp_path, monkeypatch):
    from nebius_cxcli import grafana_access

    def unexpected(*args):
        pytest.fail("Preview must not inspect access or persist kubeconfig")

    monkeypatch.setattr(grafana_access, "complete_grafana_handoff", unexpected)
    _exercise_adapter(tmp_path, monkeypatch, soperator=True, preview=True, interrupt=False)


def test_narrowing_to_ordinary_target_cannot_mutate_unselected_soperator(tmp_path, monkeypatch):
    _exercise_adapter(
        tmp_path, monkeypatch, soperator=True, preview=False, interrupt=False, target_ref="ordinary"
    )


@pytest.mark.parametrize("terraform_required", [False, True])
def test_recovery_prepares_restored_runtime_without_trusting_cached_admission(
    tmp_path, monkeypatch, terraform_required
):
    from nebius_cxcli.deployment_recovery import capture_execution_cache
    from nebius_cxcli.soperator_receipt_io import write_owner_only_json

    desired = config()
    restored = copy.deepcopy(desired)
    restored["apps"]["charts"][0]["values"]["settings"]["limit"] = 2
    restored["infra"]["components"][0]["enabled"] = terraform_required
    current_paths = paths(tmp_path / "current")
    manifest = generation(desired).materialize(current_paths)
    checkpoint_paths = paths(tmp_path / "checkpoint")
    generation(restored, content="intermediate").materialize(checkpoint_paths)
    write_owner_only_json(
        checkpoint_paths.reports_dir / "compatibility-admission.json", {"admitted": False}
    )
    cache = capture_execution_cache(checkpoint_paths.repo_root)
    executor = deployment_cli._CliDeploymentExecutor(
        runtime_config_from_manifest(manifest),
        current_paths,
        manifest,
        options=deployment_cli.DeployOptions(),
        lease=None,
    )
    fresh_admission = {"admitted": True, "matrix_sha256": "fresh"}
    executor.compatibility_report = fresh_admission
    executor._runtime_payload_env = {"PAYLOAD": "fixture"}
    executor.runtime_env = {"STALE": "discard"}
    calls = []

    def runtime_env(payload):
        assert payload == restored
        calls.append("runtime")
        return {"CONFIGURATION": "restored"}

    def backup(payload, **kwargs):
        assert payload == restored
        calls.append("backup")

    def sssd(payload, **kwargs):
        assert payload == restored
        calls.append("sssd")
        return {"SSSD": "restored"}

    expected_env = {"CONFIGURATION": "restored", "PAYLOAD": "fixture", "SSSD": "restored"}

    def initialize(directory, *, extra_env):
        assert (directory / "main.tf").read_text() == "intermediate"
        assert extra_env == expected_env
        calls.append("init")

    def validate(directory, *, extra_env, initialize):
        assert calls[-1] == "init"
        assert (directory / "main.tf").read_text() == "intermediate"
        assert extra_env == expected_env
        assert initialize is False
        calls.append("validate")

    monkeypatch.setattr(cli, "_terraform_runtime_env", runtime_env)
    monkeypatch.setattr(cli, "preflight_soperator_backup_inputs", backup)
    monkeypatch.setattr(cli, "preflight_soperator_sssd_inputs", sssd)
    monkeypatch.setattr(cli, "_required_runtime_component_output_specs", lambda _: [])
    monkeypatch.setattr(cli, "terraform_init", initialize)
    monkeypatch.setattr(cli, "terraform_validate", validate)

    def preflight(payload, restored_paths, **kwargs):
        assert payload == restored
        assert (restored_paths.infra_dir / "main.tf").read_text() == "intermediate"
        calls.append("preflight")
        if terraform_required:
            initialize(restored_paths.infra_dir, extra_env=expected_env)
            validate(restored_paths.infra_dir, extra_env=expected_env, initialize=False)
        write_owner_only_json(
            restored_paths.reports_dir / "compatibility-admission.json", fresh_admission
        )
        return {"PAYLOAD": "fixture"}

    monkeypatch.setattr(cli, "_run_deploy_preflight", preflight)
    executor.restore_execution(cache)
    assert calls == []  # No credentials or preflight of the wrong generation.
    executor.preflight()
    assert executor.config == restored
    assert executor.manifest["runtime_config"] == restored
    assert executor.runtime_env == expected_env
    assert executor.compatibility_report == fresh_admission
    assert calls == ["preflight"] + (["init", "validate"] if terraform_required else []) + [
        "runtime",
        "backup",
        "sssd",
    ]


@pytest.mark.parametrize("real_paths", [False, True])
def test_target_options_are_validated_before_backend_side_effects(
    monkeypatch, tmp_path, real_paths
):
    project_paths = paths(tmp_path) if real_paths else None
    monkeypatch.setattr(
        cli,
        "_ensure_terraform_backend_ready",
        lambda _: pytest.fail("backend changed before target validation"),
    )
    with pytest.raises((ValueError, RuntimeError), match="target|targets"):
        deployment_cli.deploy_rendered_bundle(
            {},
            project_paths,
            {"deploy": {"targets": []}},
            options=deployment_cli.DeployOptions(target_ref="missing", all_targets=True),
        )
    if project_paths is not None:
        assert not project_paths.reports_dir.exists()


def test_release_resume_uses_checks_proposal_from_immutable_deployment(tmp_path, monkeypatch):
    desired = config()
    desired["client_info"]["notifications"] = {}
    monkeypatch.setattr(cli, "_ensure_project_auth_identity", lambda **_: None)
    values = desired["apps"]["charts"][0]["values"]
    values["soperator-activechecks"] = {
        "enabled": False,
        "checks": {"gpu": {"enabled": True, "runAfterCreation": False}},
    }
    frozen = generation(copy.deepcopy(desired))
    expected = cli.freeze_checks_proposal(values)
    # An interrupted release has already materialized the policy target locally.
    resumed = copy.deepcopy(desired)
    resumed["apps"]["charts"][0]["values"], _ = cli.apply_checks_proposal(values, expected)
    executor = deployment_cli._CliDeploymentExecutor(
        resumed,
        paths(tmp_path),
        frozen.manifest,
        options=deployment_cli.DeployOptions(),
        lease=None,
    )
    executor.generation = frozen
    executor.plan = SimpleNamespace(
        target_ref="cluster", target_release="1.22.3", changed_fields=("values",)
    )
    executor.paths.config_path.parent.mkdir(parents=True, exist_ok=True)
    executor.paths.config_path.write_text(cli.render_updated_source_payload(resumed))
    # Runtime manifests expand app defaults; replay must retain the exact source
    # config that produced the saved release admission, including ordinary apps.
    executor.config = copy.deepcopy(resumed)
    executor.config["apps"]["charts"].append(
        {"id": "grafana", "instance_id": "cluster", "values": {"generated": True}}
    )
    target = object()
    monkeypatch.setattr(
        cli, "_resolve_soperator_command_target", lambda *a, **kw: (target, None, False)
    )
    monkeypatch.setattr(
        cli, "_source_helm_chart_row", lambda payload, _: payload["apps"]["charts"][0]
    )
    calls = []
    # This test isolates immutable check-policy inputs; release authority is
    # exercised through the real common engine in test_deployment_release_authority.
    monkeypatch.setattr(
        deployment_cli,
        "use_generation_soperator_releases",
        lambda *a, **kw: nullcontext(
            {"cluster": SimpleNamespace(snapshot_sha256="sha256:" + "a" * 64)}
        ),
    )
    monkeypatch.setattr(cli, "_run_common_soperator_release_upgrade", lambda **kw: calls.append(kw))
    executor._release(dry_run=False)
    assert calls[0]["source_payload"] == resumed
    assert calls[0]["checks_policy_proposal"] == expected
    assert calls[0]["checks_policy_proposal"] != cli.freeze_checks_proposal(
        resumed["apps"]["charts"][0]["values"]
    )


@pytest.mark.parametrize("target_ref", ["", "cluster"])
def test_lost_fence_after_refresh_prevents_deployment_dispatch(monkeypatch, tmp_path, target_ref):
    executor = object.__new__(deployment_cli._CliDeploymentExecutor)
    executor.plan = SimpleNamespace(action=DeploymentAction.INSTALL, target_ref=target_ref)
    executor.campaign_intent = None
    held = True

    def assert_held():
        if not held:
            raise RuntimeError("deployment fence lost")

    def refresh(**kwargs):
        nonlocal held
        held = False
        return None, {"resource_changes": []}

    executor.lease = SimpleNamespace(assert_held=assert_held, operation_id="operation")
    executor.cli = cli
    executor.config = {}
    executor.paths = paths(tmp_path)
    executor.manifest = {}
    executor.options = deployment_cli.DeployOptions()
    monkeypatch.setattr(
        cli, "_deploy_generated_artifacts", lambda *a, **kw: pytest.fail("unfenced dispatch")
    )
    executor._terraform_plan = refresh
    with pytest.raises(RuntimeError, match="deployment fence lost"):
        executor.execute(
            SimpleNamespace(
                stage=SimpleNamespace(name="reconcile"), terraform={"resource_changes": []}
            ),
            recovering=False,
        )


@pytest.mark.parametrize("loss_phase", ["preflight", "stream", "timeout", "os-error", "status"])
def test_apply_fence_and_status_abort_share_one_guard(monkeypatch, tmp_path, loss_phase):
    held = True

    def assert_held():
        if not held:
            if loss_phase == "timeout":
                raise subprocess.TimeoutExpired("lease check", timeout=60)
            if loss_phase == "os-error":
                raise OSError("lease transport failed")
            raise RuntimeError("deployment fence lost")

    def preflight(*args, **kwargs):
        nonlocal held
        if loss_phase == "preflight":
            held = False

    @contextmanager
    def reporting(*args, **kwargs):
        yield SimpleNamespace(
            handle_terraform_event=lambda _: None,
            abort_reason=lambda: "provider failed" if loss_phase == "status" else None,
            snapshot=lambda: "offline",
        )

    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _: {})
    monkeypatch.setattr(cli, "_validate_mysterybox_runtime_payload_values", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_raise_on_terraform_gpu_fabric_drift", preflight)
    monkeypatch.setattr(cli, "deployment_status_reporting", reporting)

    def apply(*args, **kwargs):
        nonlocal held
        assert loss_phase != "preflight", "unfenced apply"
        if loss_phase in {"stream", "timeout", "os-error"}:
            held = False
        reason = kwargs["abort_check"]()
        assert reason == (
            "Deployment authority lost; rerun the matching deployment."
            if loss_phase in {"stream", "timeout", "os-error"}
            else "provider failed"
        )
        raise RuntimeError("apply aborted")

    monkeypatch.setattr(cli, "terraform_apply", apply)
    expected = "deployment fence lost" if loss_phase == "preflight" else "apply aborted"
    with pytest.raises(RuntimeError, match=expected):
        cli._run_terraform_apply_with_status(
            {},
            paths(tmp_path),
            initialize=False,
            run_mk8s_preflight=False,
            assert_authority=assert_held,
        )


@pytest.mark.parametrize("late_failure", ["verify_final", "accept"])
@pytest.mark.parametrize("publication_failure", [False, True])
def test_late_failure_updates_durable_report_without_masking_error(
    tmp_path, monkeypatch, late_failure, publication_failure
):
    _exercise_adapter(
        tmp_path,
        monkeypatch,
        soperator=False,
        preview=False,
        interrupt=False,
        late_failure=late_failure,
        publication_failure=publication_failure,
    )


@pytest.mark.parametrize("late_failure", ["verify_final", "accept"])
def test_late_failure_survives_footer_error(tmp_path, monkeypatch, late_failure):
    _exercise_adapter(
        tmp_path,
        monkeypatch,
        soperator=False,
        preview=False,
        interrupt=False,
        late_failure=late_failure,
        footer_failure=True,
    )


@pytest.mark.parametrize(
    ("rendered_profile", "execution_profile"),
    [
        ("fast-dev-test", "fast-dev-test"),
        ("standard", "standard"),
        ("standard", "fast-dev-test"),
        ("fast-dev-test", "standard"),
    ],
)
def test_recovery_timing_policy_follows_frozen_effective_target(
    tmp_path, monkeypatch, rendered_profile, execution_profile
):
    from nebius_cxcli.deployment_recovery import execution_checkpoint
    from nebius_cxcli.deployment_timing import _RECORDER, timed_phase
    from nebius_cxcli.soperator_receipt_io import write_owner_only_json

    payload = config()
    payload["apps"]["charts"][0]["values"]["deploymentProfile"] = rendered_profile
    monkeypatch.setitem(globals(), "config", lambda: copy.deepcopy(payload))
    from dataclasses import replace

    original_generation = generation

    def selected_generation(payload, content="initial"):
        frozen = original_generation(payload, content)
        return replace(
            frozen,
            files={
                **frozen.files,
                "flux/kustomization.yaml": base64.b64encode(
                    b"apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: []\n"
                ).decode(),
            },
            manifest={
                **frozen.manifest,
                "deploy": {
                    "targets": [
                        {
                            "instance_id": "cluster",
                            "target_ref": "cluster",
                            "component_id": "mk8s",
                            "access": "external",
                            "cluster_id_output_name": "cluster_id",
                            "component_output_ref": "mk8s.cluster.cluster_id",
                            "flux_dir": "flux",
                        }
                    ]
                },
            },
        )

    monkeypatch.setitem(globals(), "generation", selected_generation)
    original_run = deployment_cli.run_deployment
    observations = []

    def run_with_policy_probe(**kwargs):
        checkpoints = []
        with execution_checkpoint(lambda: checkpoints.append("checkpoint")):
            with timed_phase("timing-policy-probe"):
                pass
            timing_count = len(checkpoints)
            write_owner_only_json(tmp_path / "policy-probe.json", {"fixture": True})
        observations.append((timing_count, len(checkpoints)))
        if len(observations) == 2:
            # The recovery policy is selected before execution. End this probe
            # here; target acceptance is independently covered by adapter tests.
            raise PolicyObserved("recovery policy observed")
        return original_run(**kwargs)

    class PolicyObserved(RuntimeError):
        pass

    monkeypatch.setattr(deployment_cli, "run_deployment", run_with_policy_probe)
    with pytest.raises(PolicyObserved, match="recovery policy observed"):
        _exercise_adapter(
            tmp_path,
            monkeypatch,
            soperator=True,
            preview=False,
            interrupt=True,
            hydrated=True,
            execution_profile=execution_profile,
        )
    expected = (0, 1) if execution_profile == "fast-dev-test" else (2, 3)
    assert len(observations) >= 2
    initial = (0, 1) if rendered_profile == "fast-dev-test" else (2, 3)
    assert observations == [initial, expected]
    assert _RECORDER.get() is None
