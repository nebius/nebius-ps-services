from __future__ import annotations

import copy
import json
import shlex
from contextlib import contextmanager, nullcontext
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner

from destroy_fakes import SETTINGS, Cloud, Store, receipt
from nebius_cxcli import cli
from nebius_cxcli import destroy_cli as owner
from nebius_cxcli.destroy import load_destroy_receipt
from nebius_cxcli.paths import ProjectPaths
from nebius_cxcli.soperator_receipt_io import write_owner_only_json
from test_destroy_storage import change, plan, refresh

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    paths = ProjectPaths(
        config_path=tmp_path / "config.yaml",
        repo_root=tmp_path,
        deployments_dir=tmp_path,
        project_dir=tmp_path,
        generated_dir=tmp_path / "generated",
        infra_dir=tmp_path / "generated/infra",
        flux_dir=tmp_path / "generated/flux",
        reports_dir=tmp_path / "generated/reports",
        path_tenant_folder="tenant",
        path_project_folder="project",
    )
    paths.config_path.write_text("version: original\n")
    for directory in [paths.infra_dir, paths.flux_dir, paths.reports_dir]:
        directory.mkdir(parents=True, exist_ok=True)
    source = {
        "client_info": {"nebius": {"project_id": "project-a"}},
        "apps": {"charts": [{"id": "soperator", "instance_id": "cluster-a"}]},
        "infra": {"components": [{"id": "mk8s", "instance_id": "cluster-a", "enabled": True}]},
    }
    config = SimpleNamespace()
    store = Store()
    cloud = Cloud()
    cloud.project_id = "project-a"
    cloud.inventory = lambda *a, **kw: copy.deepcopy(receipt().approved["inventory"])
    cloud.close = lambda: None
    cloud.discover_vm_nfs = lambda _config: None
    cloud.verify_vm_nfs = lambda _value: None
    calls = []
    monkeypatch.setattr(
        "nebius_cxcli.deployment_local.LocalObjectStore.for_project", lambda _: store
    )
    monkeypatch.setattr(owner, "backend_settings_from_config", lambda _: SETTINGS)
    monkeypatch.setattr(owner, "DestroyCloud", lambda *a, **kw: cloud)
    monkeypatch.setattr(owner, "runtime_config_from_manifest", lambda _: config)
    monkeypatch.setattr(
        owner, "soperator_registration_app_row", lambda *a, **kw: source["apps"]["charts"][0]
    )
    monkeypatch.setattr(cli, "load_config", lambda *a, **kw: config)
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", lambda *a, **kw: None)
    monkeypatch.setattr(
        cli,
        "_deployment_execution",
        lambda **kw: nullcontext(SimpleNamespace(assert_held=lambda: None)),
    )
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _: paths)
    monkeypatch.setattr(cli, "read_soperator_operation_status", lambda **kw: None)
    monkeypatch.setattr(cli, "_load_source_payload", lambda _: copy.deepcopy(source))
    manifest = {
        "deploy": {
            "targets": [
                {
                    "target_ref": "cluster-a",
                    "instance_id": "cluster-a",
                    "component_id": "mk8s",
                    "ownership": "managed",
                    "cluster_id_output_name": "cluster_a_cluster_id",
                    "component_output_ref": "cluster-a.cluster_id",
                    "access": "external",
                    "flux_dir": "flux/targets/cluster-a",
                }
            ]
        }
    }
    monkeypatch.setattr(cli, "_load_deploy_context_readonly", lambda _: (config, paths, manifest))
    monkeypatch.setattr(cli, "_deploy_report_target_contexts", lambda _: {})
    monkeypatch.setattr(
        cli,
        "_generated_bundle_module_sources",
        lambda _: [
            {
                "component_id": "mk8s",
                "instance_id": "cluster-a",
                "module_name": "cluster_a",
                "source": "module",
            }
        ],
    )
    monkeypatch.setattr(cli, "_source_helm_chart_row", lambda *a: {"values": {}})
    monkeypatch.setattr(owner, "cleanup_payload", lambda **kw: {"version": "cleaned"})
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _: {})
    monkeypatch.setattr(cli, "terraform_init", lambda *a, **kw: calls.append("init"))
    monkeypatch.setattr(cli, "terraform_apply", lambda *a, **kw: calls.append(("apply", kw)))
    initial = plan()

    def make_plan(directory, **kw):
        assert not kw.get("destroy") and not kw.get("targets")
        kw["plan_file"].write_bytes(b"saved exact plan")
        calls.append(("plan", kw))

    monkeypatch.setattr(cli, "terraform_plan", make_plan)

    def show(directory, **kw):
        if not kw.get("plan_file"):
            values = copy.deepcopy(initial["planned_values"])
            if "mk8scluster-a" in cloud.present:
                values["root_module"]["resources"].append(
                    {
                        "address": "module.cluster_a.nebius_mk8s_v1_cluster.this",
                        "type": "nebius_mk8s_v1_cluster",
                        "mode": "managed",
                        "values": {"id": "mk8scluster-a"},
                    }
                )
                values["outputs"]["cluster_a_cluster_id"] = {"value": "mk8scluster-a"}
            return {"values": values}
        result = copy.deepcopy(initial)
        if "mk8scluster-a" not in cloud.present:
            result["resource_drift"] = [
                row
                for row in result["resource_changes"]
                if row["type"] in {"nebius_mk8s_v1_cluster", "nebius_compute_v1_gpu_cluster"}
                and row["change"]["before"]["id"] not in cloud.present
            ]
            result["resource_changes"] = [
                row for row in result["resource_changes"] if row not in result["resource_drift"]
            ]
        return result

    monkeypatch.setattr(cli, "terraform_show_json", show)

    def stage(**kw):
        from dataclasses import replace

        staged = replace(
            paths,
            generated_dir=tmp_path / "staged",
            infra_dir=tmp_path / "staged/infra",
            flux_dir=tmp_path / "staged/flux",
            reports_dir=tmp_path / "staged/reports",
        )
        for directory in [staged.infra_dir, staged.flux_dir, staged.reports_dir]:
            directory.mkdir(parents=True, exist_ok=True)
        (staged.infra_dir / "main.tf").write_text("# final generation\n")
        from nebius_cxcli.generated_manifest import manifest_path_for_generated_dir

        manifest_path_for_generated_dir(staged.generated_dir).write_text(
            json.dumps({"deploy": {"targets": []}})
        )
        return SimpleNamespace(
            staged_paths=staged, proposed_config_text="version: cleaned\n", cleanup=lambda: None
        )

    monkeypatch.setattr(cli, "_render_soperator_upgrade_admission", stage)
    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    # Any former Kubernetes cleanup entry point is forbidden in this workflow.
    for symbol in (
        "_prepare_cluster_handoff_kube_env",
        "collect_kubectl_soperator_snapshot",
        "_destroy_rendered_flux_bundle",
    ):
        monkeypatch.setattr(
            cli, symbol, lambda *a, **kw: pytest.fail("Kubernetes must not be contacted")
        )
    return SimpleNamespace(
        paths=paths,
        cloud=cloud,
        store=store,
        calls=calls,
        plan=initial,
        source=source,
        manifest=manifest,
    )


def invoke(project, *options, phrase="destroy mk8scluster-a"):
    return runner.invoke(
        cli.app,
        ["destroy", str(project.paths.config_path), "--target", "mk8scluster-a", *options],
        input=phrase + "\n",
    )


def unstarted_v2_preview(*, status="failed"):
    # The former engine wrote approval before every destructive callback. Its
    # preapproval TTY/confirmation failures left this envelope with no progress.
    return {
        "schema": "nebius-cxcli.destroy.v2",
        "target_ref": "cluster-a",
        "ownership": "managed",
        "project_id": "project-a",
        "cluster_id": "old-cluster-id",
        "kubernetes_uid": "old-kubernetes-uid",
        "destroy_inventory": ["old-workload"],
        "preserve_inventory": ["old-filesystem"],
        "protected_storage_sha256": "sha256:" + "1" * 64,
        "infrastructure_receipt": {"old": "unused-evidence"},
        "config_sha256": "sha256:" + "2" * 64,
        "post_cleanup_config_sha256": "sha256:" + "3" * 64,
        "approval_fingerprint": "sha256:" + "4" * 64,
        "checkpoints": [],
        "delete_operation_id": "",
        "status": status,
        "failure_classification": "runtime-error" if status == "failed" else "",
    }


@pytest.mark.parametrize("preserve", [True, False])
def test_cli_deletes_gpu_and_applies_pvc_flag_through_publication(project, preserve):
    from test_destroy_resources import execution_fixture

    cloud, _ = execution_fixture()
    project.cloud.data, project.cloud.present = cloud.data, cloud.present

    def inventory(*args, **kwargs):
        assert kwargs["managed_gpu_ids"] == ["gpu_cluster-a"]
        assert kwargs["preserve_pvc_disks"] == preserve
        return cloud.inventory(*args, **kwargs)

    project.cloud.inventory = inventory
    project.plan["resource_changes"].append(
        change(
            'module.cluster_a.nebius_compute_v1_gpu_cluster.this["fabric"]',
            "nebius_compute_v1_gpu_cluster",
            "gpu_cluster-a",
        )
    )
    # The selected module's GPU state is also a source of discovery authority.
    project.plan["planned_values"]["root_module"]["resources"] = [
        {
            "address": 'module.cluster_a.nebius_compute_v1_gpu_cluster.this["fabric"]',
            "type": "nebius_compute_v1_gpu_cluster",
            "mode": "managed",
            "values": {"id": "gpu_cluster-a"},
        }
    ]
    # Planned values represent the final remaining generation; state has the GPU.
    original_show = cli.terraform_show_json

    def show(directory, **kwargs):
        result = copy.deepcopy(original_show(directory, **kwargs))
        if kwargs.get("plan_file"):
            result["planned_values"]["root_module"]["resources"] = []
        elif "gpu_cluster-a" not in project.cloud.present:
            result["values"]["root_module"]["resources"] = []
        return result

    from unittest.mock import patch

    phrase = "destroy mk8scluster-a and delete 1 GPU cluster"
    if not preserve:
        phrase += " and delete 2 PVC disks"
    with patch.object(cli, "terraform_show_json", show):
        result = invoke(project, *(["--preserve-pvc-disks"] if preserve else []), phrase=phrase)
    assert result.exit_code == 0, result.output
    assert [k for k, *_ in project.cloud.calls] == [
        "cluster",
        "gpu_cluster",
        *([] if preserve else ["disk", "disk"]),
    ]
    assert project.paths.config_path.read_text() == "version: cleaned\n"
    value = load_destroy_receipt(owner.destroy_receipt_path(project.paths, "mk8scluster-a"))
    assert value.status == "complete" and value.approved["preserve_pvc_disks"] == preserve


@pytest.mark.parametrize("schema", ["v2", "v3", "v99", "predecessor", None])
@pytest.mark.parametrize("status", ["planned", "failed", "running", "complete"])
@pytest.mark.parametrize("backend", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_unsupported_receipts_reject_every_status_without_mutation(
    project, monkeypatch, schema, status, backend, dry_run
):
    from nebius_cxcli.deployment_state import ObjectVersion
    from nebius_cxcli.destroy import digest
    from nebius_cxcli.destroy_state import DestroyState

    payload = unstarted_v2_preview(status=status) if schema == "v2" else receipt().as_payload()
    payload["schema"] = (
        "nebius-cxcli.soperator-destroy.v4"
        if schema == "predecessor"
        else f"nebius-cxcli.destroy.{schema}"
        if schema
        else None
    )
    payload["status"] = status
    if "approved" in payload:
        payload["approved"]["schema"] = payload["schema"]
        payload["approval_fingerprint"] = digest(payload["approved"])
    if status == "complete":
        payload["checkpoints"] = [
            "approved",
            "cluster_absent",
            "storage_resolved",
            "state_reconciled",
            "config_committed",
            "baseline_cleared",
        ]
    path = owner.destroy_receipt_path(project.paths, "mk8scluster-a")
    if backend:
        state = DestroyState(project.store, SETTINGS)
        project.store.values[state.key] = ObjectVersion(payload, "etag")
    else:
        write_owner_only_json(path, payload)
    before = copy.deepcopy(project.store.values)
    original = path.read_bytes() if path.exists() else None
    monkeypatch.setattr(owner, "DestroyCloud", lambda *a, **kw: pytest.fail("cloud reached"))
    result = invoke(project, *(["--dry-run"] if dry_run else []))
    assert result.exit_code == 1 and "Unsupported destroy receipt" in result.output
    assert "only v1" in result.output
    assert project.store.values == before and not project.store.writes and not project.cloud.calls
    assert (path.read_bytes() if path.exists() else None) == original


def test_v1_envelope_rejects_an_old_approval_schema(project):
    from nebius_cxcli.destroy import digest

    payload = receipt().as_payload()
    payload["approved"]["schema"] = "nebius-cxcli.destroy.v3"
    payload["approval_fingerprint"] = digest(payload["approved"])
    path = owner.destroy_receipt_path(project.paths, "mk8scluster-a")
    write_owner_only_json(path, payload)
    result = invoke(project)
    assert result.exit_code == 1 and "only v1" in result.output
    assert not project.store.writes and not project.cloud.calls


def test_v1_preview_survives_failed_fresh_planning(project, monkeypatch):
    path = owner.destroy_receipt_path(project.paths, "mk8scluster-a")
    write_owner_only_json(path, receipt().as_payload())
    original = path.read_bytes()

    def fail(*a, **kw):
        raise RuntimeError("fresh inventory unavailable")

    monkeypatch.setattr(project.cloud, "inventory", fail)
    result = invoke(project)
    assert result.exit_code == 1 and "fresh inventory unavailable" in result.output
    assert path.read_bytes() == original
    assert not project.store.writes and not project.cloud.calls


def test_v1_preview_does_not_supply_delete_sfs_confirmation(project):
    path = owner.destroy_receipt_path(project.paths, "mk8scluster-a")
    write_owner_only_json(path, receipt().as_payload())
    result = invoke(project, "--delete-sfs", phrase="destroy old-cluster-id")
    assert result.exit_code == 1 and "confirmation" in result.output.lower()
    assert not project.store.writes and not project.cloud.calls


def test_cache_advancement_during_lease_acquisition_blocks_inventory(project, monkeypatch):
    path = owner.destroy_receipt_path(project.paths, "mk8scluster-a")
    payload = receipt().as_payload()
    write_owner_only_json(path, payload)

    @contextmanager
    def advance(**kw):
        write_owner_only_json(path, {**payload, "checkpoints": ["approved"]})
        yield SimpleNamespace(assert_held=lambda: None)

    monkeypatch.setattr(cli, "_deployment_execution", advance)
    monkeypatch.setattr(owner, "DestroyCloud", lambda *a, **kw: pytest.fail("cloud reached"))
    result = invoke(project)
    assert result.exit_code == 1 and "cache changed" in result.output.lower()
    assert json.loads(path.read_text())["checkpoints"] == ["approved"]
    assert not project.store.writes and not project.cloud.calls


@pytest.mark.parametrize("dry_run", [False, True])
def test_preview_publication_refuses_local_cache_advancement(project, monkeypatch, dry_run):
    path = owner.destroy_receipt_path(project.paths, "mk8scluster-a")
    payload = receipt().as_payload()
    write_owner_only_json(path, payload)
    original_inventory = project.cloud.inventory

    def advance(*a, **kw):
        write_owner_only_json(path, {**payload, "checkpoints": ["approved"]})
        return original_inventory(*a, **kw)

    monkeypatch.setattr(project.cloud, "inventory", advance)
    result = invoke(project, *(("--dry-run",) if dry_run else ()))
    assert result.exit_code == 1 and "cache changed" in result.output.lower()
    assert json.loads(path.read_text())["checkpoints"] == ["approved"]
    assert not project.store.writes and not project.cloud.calls


def test_dry_run_is_local_and_next_execution_rebuilds_preview(project):
    result = invoke(project, "--dry-run")
    assert result.exit_code == 0, result.output
    assert not project.store.writes and not project.cloud.calls
    assert project.paths.config_path.read_text() == "version: original\n"
    result = invoke(project)
    assert result.exit_code == 0, result.output
    assert len(project.cloud.calls) == 1
    assert project.paths.config_path.read_text() == "version: cleaned\n"
    assert (
        load_destroy_receipt(owner.destroy_receipt_path(project.paths, "mk8scluster-a")).status
        == "complete"
    )
    assert len([row for row in project.calls if isinstance(row, tuple) and row[0] == "apply"]) == 1


def test_destroy_keeps_raw_terraform_values_out_of_its_progress_output(project, monkeypatch):
    for name in ("terraform_init", "terraform_plan", "terraform_show_json"):
        original = getattr(cli, name)

        def command(*args, _original=original, **kwargs):
            if not kwargs.get("quiet"):
                print("RAW_TERRAFORM_RESOURCE_VALUES")
            return _original(*args, **kwargs)

        monkeypatch.setattr(cli, name, command)
    result = invoke(project)
    assert result.exit_code == 0, result.output
    assert "RAW_TERRAFORM_RESOURCE_VALUES" not in result.output
    assert "DESTROY:" in result.output and "Type exactly" in result.output
    applied = next(row[1] for row in project.calls if isinstance(row, tuple) and row[0] == "apply")
    assert callable(applied["event_callback"]) and callable(applied["abort_check"])
    assert applied["expected_plan_sha256"].startswith("sha256:")


def test_destroy_accepts_refreshed_deletions_in_preview_and_reconciliation(project, monkeypatch):
    original = cli.terraform_show_json

    def observed(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("plan_file"):
            result.setdefault("resource_drift", []).extend(
                refresh(row) for row in result["resource_changes"]
            )
        return result

    monkeypatch.setattr(cli, "terraform_show_json", observed)
    result = invoke(project)
    assert result.exit_code == 0, result.output
    assert "Type exactly" in result.output and len(project.cloud.calls) == 1


@pytest.mark.parametrize("command", ["init", "plan", "show_json", "apply"])
def test_destroy_terraform_failures_do_not_disclose_provider_values(project, monkeypatch, command):
    def fail(*a, **kw):
        raise RuntimeError("RAW_PROVIDER_FAILURE_VALUES")

    monkeypatch.setattr(cli, "terraform_" + command, fail)
    result = invoke(project)
    assert result.exit_code == 1
    assert "RAW_PROVIDER_FAILURE_VALUES" not in result.output
    assert "Terraform" in result.output and "failed during destroy" in result.output


@pytest.mark.parametrize("onboarded", [False, True])
def test_explicit_delete_sfs_phrase_and_same_cluster_sdk_path(project, monkeypatch, onboarded):
    if onboarded:
        project.source["infra"]["components"] = []
        project.source["deploy"] = {
            "targets": [
                {"instance_id": "cluster-a", "kind": "external-mk8s", "cluster_id": "mk8scluster-a"}
            ]
        }
        project.manifest["deploy"]["targets"][0].update(
            kind="external-mk8s", ownership="external", cluster_id="mk8scluster-a"
        )
        monkeypatch.setattr(cli, "_generated_bundle_module_sources", lambda _: [])
        project.plan["resource_changes"] = []
    result = invoke(project, "--delete-sfs", phrase="destroy mk8scluster-a and delete 1 SFS")
    assert result.exit_code == 0, result.output
    assert [call[0] for call in project.cloud.calls] == ["cluster", "filesystem"]


def test_tty_observation_at_prompt_is_reused_after_progress_resumes(project, monkeypatch):
    calls = []

    def terminal():
        calls.append(True)
        return len(calls) == 1

    monkeypatch.setattr(cli, "_is_tty_session", terminal)
    monkeypatch.setattr(
        cli,
        "progress_console",
        Console(file=StringIO(), force_terminal=True, _environ={"TERM": "xterm"}),
    )
    result = invoke(project)
    assert result.exit_code == 0, result.output
    assert calls == [True]


def test_backend_receipt_resumes_after_local_cache_loss(project):
    project.cloud.fail_poll = True
    result = invoke(project)
    assert result.exit_code == 1 and "poll timeout" in result.output
    assert "Provider operation for mk8scluster-a: operation-cluster" in result.output
    assert "Resume:\nnebius-cxcli destroy" in result.output
    owner.destroy_receipt_path(project.paths, "mk8scluster-a").unlink()
    project.cloud.fail_poll = False
    result = invoke(project, phrase="")
    assert result.exit_code == 0, result.output
    assert len(project.cloud.calls) == 1
    assert "Type exactly" not in result.output


def test_destroy_failure_highlights_complete_resume_command(project, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    output = StringIO()
    console = Console(file=output, force_terminal=True, color_system="truecolor", width=40)
    monkeypatch.setattr(cli, "console", console)
    project.cloud.fail_poll = True
    flags = ["--yes", "--delete-sfs", "--preserve-pvc-disks"]
    result = invoke(project, *flags, phrase="destroy mk8scluster-a and delete 1 SFS")
    assert result.exit_code == 1
    command = shlex.join(
        [
            "nebius-cxcli",
            "destroy",
            str(project.paths.config_path),
            "--target",
            "mk8scluster-a",
            *flags,
        ]
    )
    text = Text.from_ansi(output.getvalue())
    start = text.plain.index(command)
    assert all(
        text.get_style_at_offset(console, i).bgcolor is not None
        for i in range(start, start + len(command))
    )
    assert text.get_style_at_offset(console, text.plain.index("Resume:")).bgcolor is None


@pytest.mark.parametrize("terminal", [False, True])
def test_progress_precedes_startup_io(tmp_path, monkeypatch, terminal):
    stream = StringIO()
    display = Console(
        file=stream,
        force_terminal=terminal,
        _environ={"TERM": "xterm"},
        color_system=None,
        width=100,
    )
    monkeypatch.setattr(cli, "progress_console", display)
    observed = []

    def fail(*args, **kwargs):
        observed.append(stream.getvalue())
        raise RuntimeError("startup failure")

    monkeypatch.setattr(cli, "load_config", fail)
    result = runner.invoke(
        cli.app, ["destroy", str(tmp_path / "config.yaml"), "--target", "mk8scluster-a"]
    )
    assert result.exit_code == 1 and "startup failure" in result.output
    assert "Reading deployment identity" in observed[0]
    assert not display._live_stack


def test_resume_uses_frozen_execution_when_local_generated_files_are_missing(project):
    project.cloud.fail_poll = True
    result = invoke(project)
    assert result.exit_code == 1
    # Existing generated files can be lost along with local receipts on another workstation.
    for file in project.paths.generated_dir.rglob("*"):
        if file.is_file():
            file.unlink()
    project.cloud.fail_poll = False
    result = invoke(project, phrase="")
    assert result.exit_code == 0, result.output
    assert (project.paths.infra_dir / "main.tf").read_text() == "# final generation\n"
    assert len(project.cloud.calls) == 1


def test_dry_run_authentication_and_loading_stay_in_preview_context(project, monkeypatch):
    from nebius_cxcli.deployment_recovery import is_deployment_preview

    observed = []

    def auth(*a, **kw):
        observed.append(is_deployment_preview())
        assert is_deployment_preview(), "dry-run must not create credentials"

    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", auth)
    result = invoke(project, "--dry-run")
    assert result.exit_code == 0, result.output
    assert observed and all(observed)
    assert not project.store.writes


def test_remote_receipt_ignores_corrupt_local_cache(project):
    project.cloud.fail_poll = True
    assert invoke(project).exit_code == 1
    owner.destroy_receipt_path(project.paths, "mk8scluster-a").write_text("corrupt")
    project.cloud.fail_poll = False
    result = invoke(project, phrase="")
    assert result.exit_code == 0, result.output
    assert len(project.cloud.calls) == 1


@pytest.mark.parametrize("restore_original", [False, True])
def test_publication_checkpoint_reestablishes_final_generation_before_completion(
    project, monkeypatch, restore_original
):
    from nebius_cxcli.deployment_state import DeploymentState

    original = DeploymentState.clear_accepted

    def fail(*a, **kw):
        raise RuntimeError("baseline interrupted")

    monkeypatch.setattr(DeploymentState, "clear_accepted", fail)
    result = invoke(project)
    assert result.exit_code == 1 and "baseline interrupted" in result.output
    if restore_original:
        project.paths.config_path.write_text("version: original\n")
    for path in project.paths.generated_dir.rglob("*"):
        if path.is_file():
            path.unlink()
    monkeypatch.setattr(DeploymentState, "clear_accepted", original)
    result = invoke(project, phrase="")
    assert result.exit_code == 0, result.output
    assert project.paths.config_path.read_text() == "version: cleaned\n"
    assert (project.paths.infra_dir / "main.tf").read_text() == "# final generation\n"
    assert len(project.cloud.calls) == 1


@pytest.mark.parametrize("ordinary", [False, True])
@pytest.mark.parametrize("onboarded", [False, True])
def test_yes_deletes_managed_and_onboarded_without_tty(project, monkeypatch, ordinary, onboarded):
    if ordinary:
        project.source["apps"]["charts"] = []
        monkeypatch.setattr(owner, "soperator_registration_app_row", lambda *a, **k: None)
    if onboarded:
        project.source["infra"]["components"] = []
        project.source["deploy"] = {
            "targets": [
                {"instance_id": "cluster-a", "kind": "external-mk8s", "cluster_id": "mk8scluster-a"}
            ]
        }
        project.manifest["deploy"]["targets"][0].update(
            kind="external-mk8s", ownership="external", cluster_id="mk8scluster-a"
        )
        monkeypatch.setattr(cli, "_generated_bundle_module_sources", lambda _: [])
        project.plan["resource_changes"] = []
    monkeypatch.setattr(cli, "_is_tty_session", lambda: pytest.fail("--yes must not request a TTY"))
    result = invoke(project, "--yes", phrase="")
    assert result.exit_code == 0, result.output
    assert [row[0] for row in project.cloud.calls] == ["cluster"]
    assert "Type exactly" not in result.output


def test_yes_dry_run_never_approves_or_deletes(project, monkeypatch):
    monkeypatch.setattr(cli, "_is_tty_session", lambda: pytest.fail("preview never prompts"))
    result = invoke(project, "--yes", "--dry-run", phrase="")
    assert result.exit_code == 0, result.output
    assert not project.cloud.calls and not project.store.writes
    assert project.paths.config_path.read_text() == "version: original\n"


def test_yes_terminal_retry_is_explicit_and_keeps_inventory(project, monkeypatch):
    from nebius_cxcli.destroy_cloud import TerminalDestroyOperationError

    original = project.cloud.poll_delete
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(
        project.cloud,
        "poll_delete",
        lambda *a: (_ for _ in ()).throw(TerminalDestroyOperationError("failed")),
    )
    result = invoke(project, "--yes", phrase="")
    assert result.exit_code == 1
    monkeypatch.setattr(project.cloud, "poll_delete", original)
    denied = invoke(project, phrase="")
    assert denied.exit_code == 1 and len(project.cloud.calls) == 1
    result = invoke(project, "--yes", phrase="")
    assert result.exit_code == 0, result.output
    assert len(project.cloud.calls) == 2


def test_completed_receipt_replay_never_resolves_removed_target(project, monkeypatch):
    assert invoke(project, "--yes").exit_code == 0
    monkeypatch.setattr(
        owner,
        "resolve_destroy_target",
        lambda **k: pytest.fail("completed replay must use receipt"),
    )
    project.source.clear()
    result = invoke(project, "--yes")
    assert result.exit_code == 0, result.output
    assert len(project.cloud.calls) == 1
    project.paths.config_path.write_text("version: replacement-cluster\n")
    result = invoke(project, "--yes")
    assert result.exit_code == 1 and "Completed destroy config differs" in result.output
    assert len(project.cloud.calls) == 1


def test_explicit_unknown_cluster_never_falls_back_to_generic_destroy(project, monkeypatch):
    monkeypatch.setattr(
        cli, "_destroy_generated_artifacts", lambda *a, **k: pytest.fail("no fallback")
    )
    result = runner.invoke(
        cli.app, ["destroy", str(project.paths.config_path), "--target", "unknown", "--yes"]
    )
    assert result.exit_code == 1 and "project-bound" in result.output
    assert not project.cloud.calls


def test_removed_command_and_bulk_option_are_rejected(project):
    assert runner.invoke(cli.app, ["soperator", "destroy", "--help"]).exit_code == 2
    result = runner.invoke(
        cli.app, ["destroy", str(project.paths.config_path), "--all-targets", "--yes"]
    )
    assert result.exit_code == 2
    assert not project.cloud.calls


@pytest.mark.parametrize("status", ["planned", "running", "complete"])
def test_predecessor_cache_filename_is_rejected_without_mutation(project, monkeypatch, status):
    path = project.paths.reports_dir / "soperator-destroy-cluster-a.json"
    payload = {
        **receipt().as_payload(),
        "schema": "nebius-cxcli.soperator-destroy.v4",
        "status": status,
    }
    write_owner_only_json(path, payload)
    original = path.read_bytes()
    monkeypatch.setattr(owner, "DestroyCloud", lambda *a, **k: pytest.fail("cloud reached"))
    result = invoke(project, "--yes")
    assert result.exit_code == 1 and "Unsupported destroy receipt" in result.output
    assert path.read_bytes() == original
    assert not project.store.writes and not project.cloud.calls
