import base64
import copy
import json
from contextlib import nullcontext
from dataclasses import asdict
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from nebius_cxcli import cli, deployment_cli, nsight_install, ordinary_apps
from nebius_cxcli.config_model import to_dynamic_payload
from nebius_cxcli.deployment_jail_state import build_jail_state_receipt
from nebius_cxcli.deployment_state import DeploymentGeneration, DeploymentState, digest
from nebius_cxcli.generated_manifest import load_generated_manifest
from nebius_cxcli.nsight_credentials import prepare_login_credentials
from nebius_cxcli.nsight_profiling import default_settings
from test_deployment_campaign import paths
from test_deployment_state import Store, settings
from test_nsight_profiling import package_receipts, profiling_config
from test_soperator_upstream_adapter import render_soperator_adapter_documents

IDENTITY = {"cluster_id": "cluster-id", "kubernetes_uid": "kube-uid"}
_apply_viewers = nsight_install.apply_viewers


def test_generated_viewer_releases_reach_the_ordinary_apply_owner(
    installed_project, monkeypatch, tmp_path
):
    from nebius_cxcli.ordinary_apps import resource_documents

    local, initial, *_ = installed_project
    generation = nsight_install.prepare_generation(
        cli, local, initial, target_ref="cluster", settings=default_settings()
    )
    local = paths(tmp_path / "apply")
    generation.materialize(local)
    monkeypatch.setattr(ordinary_apps, "validate_live_app_ownership", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "app_mutation_scope", lambda *a, **kw: nullcontext())
    selected = []
    monkeypatch.setattr(
        cli,
        "_apply_rendered_flux",
        lambda paths, **kw: selected.extend(resource_documents(paths.flux_dir)),
    )
    _apply_viewers(
        cli,
        local,
        generation.manifest["runtime_config"],
        target_ref="cluster",
        env={},
        fence=lambda: None,
    )
    releases = [row for row in selected if row["kind"] == "HelmRelease"]
    assert {row["metadata"]["name"] for row in releases} == {
        "nsight-streamer",
        "nsight-streamer-ncu",
    }
    assert all("releaseName" not in row["spec"] for row in releases)


def test_failed_install_recovery_uses_frozen_owner_and_does_not_accept(
    installed_project, monkeypatch
):
    from nebius_cxcli import nsight_recover_command, nsight_recovery

    local, _, state, jobs, verified, applied = installed_project
    original_wait = cli._wait_protected_data_plane_job

    def interrupted(**kwargs):
        if kwargs["name"].endswith("install"):
            raise RuntimeError("terminal failure")
        return original_wait(**kwargs)

    monkeypatch.setattr(cli, "_wait_protected_data_plane_job", interrupted)
    with pytest.raises(RuntimeError, match="terminal failure"):
        nsight_install.install_profiling(
            local.config_path,
            target_ref="cluster",
            secret_name="nsight-streamer-auth",
            reports_path="/data/nsight-reports",
        )
    initial = copy.deepcopy(state.read().value)
    assert initial["active"]["stages"]["nsight-install"]["attempts"][0]["jobUid"] == "job-uid"
    monkeypatch.setattr(
        "nebius_cxcli.deployment_local.LocalObjectStore.for_project", lambda _: state.store
    )
    lease = SimpleNamespace(assert_held=lambda: None, authority=SimpleNamespace(fencing_epoch=2))
    monkeypatch.setattr(
        nsight_recover_command, "SoperatorOperationLease", lambda **kw: nullcontext(lease)
    )

    def proof(kube, attempt):
        body = {
            "jobUid": attempt["jobUid"],
            "workloadSha256": attempt["workloadSha256"],
            "podUid": "pod",
            "podIdentitySha256": digest("pod"),
            "terminated": {"container": "done"},
        }
        return {**body, "proofSha256": digest(body)}

    monkeypatch.setattr(nsight_recovery, "terminal_proof", proof)
    nsight_recover_command.recover_profiling(
        local.config_path, target_ref="cluster", stage="install", job_uid="job-uid", dry_run=True
    )
    assert state.read().value == initial and len(jobs) == 2
    monkeypatch.setattr(cli, "_wait_protected_data_plane_job", original_wait)
    monkeypatch.setattr(
        cli,
        "_ensure_protected_data_plane_job",
        lambda job, **kw: (
            jobs.append(job)
            or ("successor" if job["metadata"]["name"].endswith("-r1") else "job-uid"),
            digest(job),
        ),
    )
    nsight_recover_command.recover_profiling(
        local.config_path, target_ref="cluster", stage="install", job_uid="job-uid"
    )
    active = state.read().value["active"]
    assert active is not None and "nsight-verify" not in active["stages"]
    assert active["stages"]["nsight-install"]["attempts"][-1]["complete"]
    assert verified == applied == []
    nsight_recover_command.recover_profiling(
        local.config_path, target_ref="cluster", stage="install", job_uid="job-uid"
    )
    assert len(jobs) == 3
    nsight_install.install_profiling(
        local.config_path,
        target_ref="cluster",
        secret_name="nsight-streamer-auth",
        reports_path="/data/nsight-reports",
    )
    assert state.read().value["active"] is None
    assert len(verified) == len(applied) == 1


@pytest.mark.parametrize("repair_kind", ["runtime-mounts", "runtime-image", "profile-order"])
def test_explicit_mount_repair_preserves_failed_history_and_normal_install_resumes(
    installed_project,
    monkeypatch,
    capsys,
    repair_kind,
):
    from nebius_cxcli import (
        nsight_mount_repair,
        nsight_profiling,
        nsight_recover_command,
        nsight_recovery,
    )

    local, _, state, jobs, _, _ = installed_project
    renderer = nsight_profiling.customization_job
    waiter = cli._wait_protected_data_plane_job
    stage = "install" if repair_kind == "profile-order" else "admit"
    stage_key = "nsight-" + stage

    def old_renderer(**kwargs):
        request = renderer(**kwargs)
        container = request["spec"]["template"]["spec"]["containers"][0]
        if repair_kind == "profile-order":
            if kwargs["request"]["action"] == "install":
                container["command"][2] = container["command"][2].replace(
                    nsight_profiling.activation_command("install"), "", 1
                )
        elif repair_kind == "runtime-image":
            container["image"] = nsight_mount_repair.INCOMPATIBLE_IMAGE
        else:
            container["securityContext"].pop("appArmorProfile")
        return request

    monkeypatch.setattr(nsight_profiling, "customization_job", old_renderer)
    monkeypatch.setattr(
        cli,
        "_wait_protected_data_plane_job",
        lambda **kw: (
            (_ for _ in ()).throw(RuntimeError("initial mount or PATH denied"))
            if kw["name"].endswith(stage)
            else waiter(**kw)
        ),
    )
    install_args = dict(
        target_ref="cluster",
        secret_name="nsight-streamer-auth",
        reports_path="/data/nsight-reports",
    )
    with pytest.raises(RuntimeError, match="initial mount"):
        nsight_install.install_profiling(local.config_path, **install_args)
    before = copy.deepcopy(state.read().value)
    monkeypatch.setattr(nsight_profiling, "customization_job", renderer)
    with pytest.raises(RuntimeError, match="Frozen Nsight"):
        nsight_install.install_profiling(local.config_path, **install_args)
    assert state.read().value == before
    prior_calls = len(jobs)
    monkeypatch.setattr(
        "nebius_cxcli.deployment_local.LocalObjectStore.for_project", lambda _: state.store
    )
    lease = SimpleNamespace(assert_held=lambda: None, authority=SimpleNamespace(fencing_epoch=2))
    monkeypatch.setattr(
        nsight_recover_command, "SoperatorOperationLease", lambda **kw: nullcontext(lease)
    )

    def proof(kube, attempt):
        body = {
            "jobUid": attempt["jobUid"],
            "workloadSha256": attempt["workloadSha256"],
            "podUid": "pod",
            "podIdentitySha256": digest("pod"),
            "terminated": {"rootfs": {"exitCode": 1}, "gate": {"exitCode": 0}},
        }
        return {**body, "proofSha256": digest(body)}

    def cause(kube, attempt, original, kind):
        body = {k: v for k, v in original.items() if k != "proofSha256"}
        signature, boundary = nsight_mount_repair.failure_signature(attempt["manifest"], kind)
        body["mountFailure"] = {
            "signatureSha256": digest(signature),
            "boundary": boundary,
        }
        return {**body, "proofSha256": digest(body)}

    monkeypatch.setattr(nsight_recovery, "terminal_proof", proof)
    monkeypatch.setattr(nsight_recovery, "add_failure_proof", cause)
    args = dict(target_ref="cluster", stage=stage, job_uid="job-uid", repair=repair_kind)
    nsight_recover_command.recover_profiling(local.config_path, dry_run=True, **args)
    assert (
        "activation" if repair_kind == "profile-order" else "Unconfined"
    ) in capsys.readouterr().out
    assert state.read().value == before and len(jobs) == prior_calls

    def lost_acknowledgment(*args, **kwargs):
        raise RuntimeError("interrupted after repair reservation")

    monkeypatch.setattr(cli, "_ensure_protected_data_plane_job", lost_acknowledgment)
    with pytest.raises(RuntimeError, match="after repair reservation"):
        nsight_recover_command.recover_profiling(local.config_path, **args)
    assert len(state.read().value["active"]["stages"][stage_key]["attempts"]) == 2
    monkeypatch.setattr(
        cli,
        "_wait_protected_data_plane_job",
        lambda **kw: waiter(**{**kw, "name": kw["name"].removesuffix("-r1")}),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_protected_data_plane_job",
        lambda job, **kw: (
            jobs.append(job)
            or ("successor" if job["metadata"]["name"].endswith("-r1") else "job-uid"),
            digest(job),
        ),
    )
    nsight_recover_command.recover_profiling(local.config_path, **{**args, "repair": ""})
    active = copy.deepcopy(state.read().value["active"])
    assert (
        active["stages"][stage_key]["attempts"][0]
        == before["active"]["stages"][stage_key]["attempts"][0]
    )
    nsight_recover_command.recover_profiling(local.config_path, **args)
    assert len(jobs) == prior_calls + 1
    nsight_install.install_profiling(local.config_path, **install_args)
    assert state.read().value["active"] is None


@pytest.fixture
def installed_project(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "nebius_cxcli.nsight_credentials.prepare_login_credentials", lambda **kw: None
    )
    monkeypatch.setattr("nebius_cxcli.installation_reconciliation._read", lambda *a: {"items": []})
    monkeypatch.setattr(
        "nebius_cxcli.nsight_runtime.NsightKubernetes.get", lambda *a: {"status": {}}
    )
    local = paths(tmp_path)
    payload = profiling_config()
    payload["client_info"]["client_name"] = "example"
    payload["apps"]["charts"] = payload["apps"]["charts"][:1]
    payload["deploy"]["targets"][0].pop("profiling")
    values = payload["apps"]["charts"][0]["values"]
    docs, adapter = render_soperator_adapter_documents(values)
    target = {
        "target_ref": "cluster",
        "instance_id": "cluster",
        "component_id": "mk8s",
        "ownership": "managed",
        "flux_dir": "flux/targets/cluster",
    }
    source = cli.render_updated_source_payload(to_dynamic_payload(payload))
    manifest = {
        "schema": "nebius-cxcli-generated/v2",
        "runtime_config": payload,
        "execution": {"backend": asdict(settings())},
        "deploy": {"targets": [target]},
        "render": {"source_config_sha256": digest(yaml.safe_load(source))},
    }
    generation = DeploymentGeneration(
        manifest,
        {
            "infra/main.tf": base64.b64encode(b"terraform {}\n").decode(),
            "flux/targets/cluster/soperator-nebius-adapter.yaml": base64.b64encode(
                yaml.safe_dump_all(docs).encode()
            ).decode(),
            "flux/targets/cluster/kustomization.yaml": base64.b64encode(
                b"resources: [soperator-nebius-adapter.yaml]\n"
            ).decode(),
        },
    )
    generation.materialize(local)
    local.config_path.write_text(source)
    storage = {
        "activePvcName": adapter["activePvc"],
        "volumes": {adapter["activePvc"]: {"pvcUid": "pvc-uid", "pvUid": "pv-uid"}},
        "directoryIdentities": [],
    }
    state = DeploymentState(
        Store(), settings(), assert_held=lambda: None, command="profiling-cluster"
    )
    record = state.begin(generation, plan={"semanticPlan": {"selectedTargets": ["cluster"]}})
    jail = build_jail_state_receipt(
        authored=generation,
        effective=generation,
        target_ref="cluster",
        identity=IDENTITY,
        storage_evidence=storage,
    )
    record = state.accept(
        record,
        evidence={
            "identities": {"cluster": IDENTITY},
            "targets": {
                "cluster": {
                    "identity": IDENTITY,
                    "generation": generation.identity,
                    "effectiveGeneration": generation.identity,
                    "jailState": jail,
                    "desiredBundle": jail["desiredBundle"],
                }
            },
        },
        derived_generations=(generation,),
    )
    ordinary_apps.accept_ordinary_app_baseline(
        local,
        identities={"cluster": IDENTITY},
        deployment_generation=generation.identity,
        expected_generation=generation,
    )

    def refresh(candidate, stage, manifest):
        return {**copy.deepcopy(manifest), "runtime_config": copy.deepcopy(candidate)}

    monkeypatch.setattr(
        "nebius_cxcli.application_compatibility.refresh_application_manifest", refresh
    )
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _: (
            load_generated_manifest(local.generated_dir)["runtime_config"],
            local,
            load_generated_manifest(local.generated_dir),
        ),
    )
    monkeypatch.setattr(cli, "_resolve_selected_deploy_targets", lambda *a, **kw: [target])
    monkeypatch.setattr(
        "nebius_cxcli.terraform_backend.backend_settings_from_config", lambda _: settings()
    )
    monkeypatch.setattr(
        "nebius_cxcli.deployment_local.LocalObjectStore.for_project", lambda _: state.store
    )
    lease = SimpleNamespace(assert_held=lambda: None, authority=SimpleNamespace(fencing_epoch=1))
    monkeypatch.setattr(
        "nebius_cxcli.deployment_cli.deployment_execution", lambda **kw: nullcontext(lease)
    )
    monkeypatch.setattr(nsight_install, "SoperatorOperationLease", lambda **kw: nullcontext(lease))
    from nebius_cxcli.grafana_runtime import GRAFANA_TARGET_KUBE_CONTEXT_ENV

    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *a, **kw: {"KUBECONFIG": "local", GRAFANA_TARGET_KUBE_CONTEXT_ENV: "cluster"},
    )
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **kw: "kube-uid")
    monkeypatch.setattr(
        "nebius_cxcli.deployment_jail_state.observe_jail_storage",
        lambda *a, **kw: copy.deepcopy(storage),
    )
    monkeypatch.setattr(nsight_install, "prepare_nsight_viewers", lambda *a, **kw: ())
    jobs, verified, applied = [], [], []
    admission, verification = package_receipts()
    monkeypatch.setattr(
        cli,
        "_ensure_protected_data_plane_job",
        lambda job, **kw: (jobs.append(job) or "job-uid", digest(job)),
    )
    monkeypatch.setattr(
        cli,
        "_wait_protected_data_plane_job",
        lambda **kw: (
            "CXCLI_NSIGHT="
            + json.dumps(admission if kw["name"].endswith("admit") else verification)
        ),
    )
    monkeypatch.setattr(
        nsight_install, "verify_active_tools", lambda env, receipt: verified.append(receipt)
    )
    monkeypatch.setattr(
        nsight_install, "apply_viewers", lambda *a, **kw: applied.append(kw["target_ref"])
    )
    monkeypatch.setattr(nsight_install, "collect_nsight_status", lambda *a, **kw: ())
    return local, generation, state, jobs, verified, applied


def test_candidate_render_and_publication_preserve_protected_files_and_disable_hooks(
    installed_project,
):
    local, initial, _, _, _, _ = installed_project
    generation = nsight_install.prepare_generation(
        cli, local, initial, target_ref="cluster", settings=default_settings()
    )
    assert generation.files["infra/main.tf"] == initial.files["infra/main.tf"]
    stages = []
    for name, content in generation.files.items():
        if "/ordinary/" not in name:
            continue
        for doc in yaml.safe_load_all(base64.b64decode(content)):
            if isinstance(doc, dict) and doc.get("kind") == "HelmRelease":
                stages.append(doc)
                assert all(
                    doc["spec"][action]["disableHooks"]
                    for action in ("install", "upgrade", "uninstall")
                )
                assert doc["spec"]["values"]["volumeMounts"][0]["subPath"] == "nsight-reports"
                assert len(doc["spec"]["postRenderers"][0]["kustomize"]["patches"]) == 3
    assert len(stages) == 2


def test_retry_after_backend_acceptance_repairs_baseline_without_reinstalling(
    installed_project, monkeypatch
):
    local, _, state, jobs, verified, applied = installed_project
    accept = ordinary_apps.accept_ordinary_app_baseline
    monkeypatch.setattr(ordinary_apps, "accept_ordinary_app_baseline", lambda *a, **kw: False)
    with pytest.raises(RuntimeError, match="baseline requires recovery"):
        nsight_install.install_profiling(
            local.config_path,
            target_ref="cluster",
            secret_name="nsight-streamer-auth",
            reports_path="/data/nsight-reports",
        )
    assert state.read().value["active"] is None
    assert len(jobs) == 3
    monkeypatch.setattr(ordinary_apps, "accept_ordinary_app_baseline", accept)
    nsight_install.install_profiling(
        local.config_path,
        target_ref="cluster",
        secret_name="nsight-streamer-auth",
        reports_path="/data/nsight-reports",
    )
    assert len(jobs) == 3 and len(verified) == 2 and applied == ["cluster", "cluster"]
    assert ".generated-staging-" not in json.dumps(load_generated_manifest(local.generated_dir))


def test_two_successful_installs_preserve_accepted_state_and_generated_bytes(
    installed_project, monkeypatch
):
    local, _, state, jobs, verified, applied = installed_project
    kwargs = dict(
        target_ref="cluster",
        secret_name="nsight-streamer-auth",
        reports_path="/data/nsight-reports",
    )
    nsight_install.install_profiling(local.config_path, **kwargs)
    before = copy.deepcopy(state.read().value)
    config = local.config_path.read_bytes()
    files = {
        p.relative_to(local.generated_dir): p.read_bytes()
        for p in local.generated_dir.rglob("*")
        if p.is_file()
    }

    def forbidden(*a, **kw):
        pytest.fail("A successful unchanged install must not begin, publish or accept again")

    for name in ("prepare_generation", "publish_generation"):
        monkeypatch.setattr(nsight_install, name, forbidden)
    monkeypatch.setattr(DeploymentState, "begin", forbidden)
    monkeypatch.setattr(DeploymentState, "accept", forbidden)
    nsight_install.install_profiling(local.config_path, **kwargs)
    assert state.read().value == before and len(jobs) == 3
    assert local.config_path.read_bytes() == config
    assert {
        p.relative_to(local.generated_dir): p.read_bytes()
        for p in local.generated_dir.rglob("*")
        if p.is_file()
    } == files
    assert len(verified) == len(applied) == 2


def test_missing_login_secret_stops_before_publication_or_installation(
    installed_project, monkeypatch
):
    from nebius_cxcli.nsight_runtime import NsightKubernetes, prepare_nsight_viewers

    local, _, state, jobs, verified, applied = installed_project
    before_config = local.config_path.read_bytes()
    before_generated = {
        p.relative_to(local.generated_dir): p.read_bytes()
        for p in local.generated_dir.rglob("*")
        if p.is_file()
    }
    before_backend = copy.deepcopy(state.read().value)
    monkeypatch.setattr(nsight_install, "prepare_nsight_viewers", prepare_nsight_viewers)

    def run(self, args, **kwargs):
        assert args[2:4] == ["get", "secret"]
        return ""

    monkeypatch.setattr(NsightKubernetes, "run", run)
    with pytest.raises(RuntimeError, match="soperator/nsight-streamer-auth.*does not exist"):
        nsight_install.install_profiling(
            local.config_path,
            target_ref="cluster",
            secret_name="nsight-streamer-auth",
            reports_path="/data/nsight-reports",
            interactive=False,
        )
    assert local.config_path.read_bytes() == before_config
    assert {
        p.relative_to(local.generated_dir): p.read_bytes()
        for p in local.generated_dir.rglob("*")
        if p.is_file()
    } == before_generated
    assert state.read().value == before_backend
    assert jobs == verified == applied == []


@pytest.mark.parametrize(
    "options,expected",
    [
        ([], (True, False)),
        (["--interactive"], (True, False)),
        (["--no-interactive"], (False, False)),
        (["--password-stdin"], (False, True)),
        (["--no-interactive", "--password-stdin"], (False, True)),
        (["--interactive", "--password-stdin"], None),
    ],
)
def test_public_cli_resolves_credential_modes(installed_project, monkeypatch, options, expected):
    local, _, _, jobs, _, _ = installed_project
    received = []
    monkeypatch.setattr(
        "nebius_cxcli.nsight_credentials.prepare_login_credentials",
        lambda **kw: received.append((kw["interactive"], kw["password_stdin"])),
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "soperator",
            "profiling",
            "install",
            str(local.config_path),
            "--target",
            "cluster",
            *options,
        ],
    )
    if expected is None:
        assert result.exit_code == 1 and "not both" in " ".join(result.output.split())
        assert received == jobs == []
    else:
        assert result.exit_code == 0, result.output
        assert received == [expected]


@pytest.mark.parametrize("terminal", [False, True])
def test_default_wizard_input_failure_precedes_publication_and_jobs(
    installed_project, monkeypatch, terminal
):
    import io

    import typer

    from nebius_cxcli import nsight_credentials

    local, _, state, jobs, verified, applied = installed_project
    before_config = local.config_path.read_bytes()
    before_state = copy.deepcopy(state.read().value)
    before_generated = {
        p.relative_to(local.generated_dir): p.read_bytes()
        for p in local.generated_dir.rglob("*")
        if p.is_file()
    }
    monkeypatch.setattr(nsight_credentials, "prepare_login_credentials", prepare_login_credentials)
    monkeypatch.setattr(nsight_credentials, "_read_secret", lambda *a: None)
    stream = io.StringIO()
    monkeypatch.setattr(stream, "isatty", lambda: terminal)
    monkeypatch.setattr(nsight_credentials.sys, "stdin", stream)

    def cancel(*a, **kw):
        raise typer.Abort()

    monkeypatch.setattr(nsight_credentials.typer, "prompt", cancel)
    monkeypatch.setattr(
        nsight_credentials.NsightKubernetes, "run", lambda *a, **kw: pytest.fail("Secret write")
    )
    error = typer.Abort if terminal else ValueError
    with pytest.raises(error) as caught:
        nsight_install.install_profiling(
            local.config_path,
            target_ref="cluster",
            secret_name="nsight-streamer-auth",
            reports_path="/data/nsight-reports",
        )
    if not terminal:
        assert "requires a terminal" in str(caught.value)
        assert "--password-stdin" in str(caught.value)
    assert before_config == local.config_path.read_bytes()
    assert before_state == state.read().value
    assert before_generated == {
        p.relative_to(local.generated_dir): p.read_bytes()
        for p in local.generated_dir.rglob("*")
        if p.is_file()
    }
    assert jobs == verified == applied == []


@pytest.mark.parametrize("terminal", [False, True])
def test_success_and_rerun_print_one_shared_password_command(
    installed_project, monkeypatch, capsys, terminal
):
    from rich.console import Console
    from rich.text import Text

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    console = Console(force_terminal=terminal, color_system="auto", width=300)
    monkeypatch.setattr(cli, "console", console)
    local, _, _, jobs, _, _ = installed_project
    command = (
        "kubectl --context verified get secret custom-login -o 'jsonpath={.data.password}'"
        " | base64 --decode && printf '\\n'"
    )
    statuses = [
        {"password_command": command, "port_forward_command": f"forward-{tool}", "url": tool}
        for tool in ("nsys", "ncu")
    ]
    monkeypatch.setattr(nsight_install, "collect_nsight_status", lambda *a, **kw: statuses)
    for _ in range(2):
        nsight_install.install_profiling(
            local.config_path,
            target_ref="cluster",
            secret_name="custom-login",
            reports_path="/data/nsight-reports",
        )
        text = Text.from_ansi(capsys.readouterr().out)
        output = text.plain
        assert output.count("To display your Nsight browser password, run:") == 1
        assert output.count(command) == 1
        assert "forward-nsys" in output and "forward-ncu" in output
        for printed in (
            command,
            "forward-nsys",
            "forward-ncu",
            "source /etc/profile.d/99-nsight.sh",
        ):
            start = output.index(printed)
            assert all(
                (text.get_style_at_offset(console, i).bgcolor is not None) == terminal
                for i in range(start, start + len(printed))
            )
    assert len(jobs) == 3


def test_failed_install_does_not_print_password_command(installed_project, monkeypatch, capsys):
    local, _, _, _, _, _ = installed_project

    def fail(*a, **kw):
        raise RuntimeError("Viewer unavailable")

    monkeypatch.setattr(nsight_install, "apply_viewers", fail)
    with pytest.raises(RuntimeError, match="Viewer unavailable"):
        nsight_install.install_profiling(
            local.config_path,
            target_ref="cluster",
            secret_name="nsight-streamer-auth",
            reports_path="/data/nsight-reports",
        )
    output = capsys.readouterr().out
    assert "To display your Nsight browser password" not in output
    assert "base64 --decode" not in output


def test_pending_configuration_is_rejected_before_jobs(installed_project):
    local, _, _, jobs, _, _ = installed_project
    payload = yaml.safe_load(local.config_path.read_text())
    payload["client_info"]["client_name"] = "changed"
    local.config_path.write_text(yaml.safe_dump(payload))
    with pytest.raises(RuntimeError, match="pending"):
        nsight_install.install_profiling(
            local.config_path,
            target_ref="cluster",
            secret_name="nsight-streamer-auth",
            reports_path="/data/nsight-reports",
        )
    assert jobs == []


def test_interrupted_job_resume_disallows_creation(installed_project, monkeypatch):
    local, _, state, _, _, _ = installed_project
    wait = cli._wait_protected_data_plane_job
    ensure = cli._ensure_protected_data_plane_job

    def interrupted(**kwargs):
        raise RuntimeError("client interrupted")

    monkeypatch.setattr(cli, "_wait_protected_data_plane_job", interrupted)
    kwargs = {
        "target_ref": "cluster",
        "secret_name": "nsight-streamer-auth",
        "reports_path": "/data/nsight-reports",
    }
    with pytest.raises(RuntimeError, match="client interrupted"):
        nsight_install.install_profiling(local.config_path, **kwargs)
    assert state.read().value["active"] is not None
    monkeypatch.setattr(cli, "_wait_protected_data_plane_job", wait)

    def resume(job, **options):
        if options["expected_job_uid"]:
            assert options["expected_job_uid"] == "job-uid"
            assert options["allow_create"] is False
        return ensure(job, **options)

    monkeypatch.setattr(cli, "_ensure_protected_data_plane_job", resume)
    nsight_install.install_profiling(local.config_path, **kwargs)
    assert state.read().value["active"] is None


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_generic_deploy_routes_active_profiling_before_recovery(
    installed_project, monkeypatch, published, dry_run
):
    local, initial, state, jobs, _, _ = installed_project
    candidate = nsight_install.prepare_generation(
        cli, local, initial, target_ref="cluster", settings=default_settings()
    )
    state.begin(
        candidate,
        plan={
            "kind": "nsight-profiling",
            "target": "cluster",
            "settings": default_settings(),
            "semanticPlan": {"selectedTargets": ["cluster"]},
        },
    )
    current = candidate if published else initial
    local = paths(local.project_dir / "retry")
    manifest = current.materialize(local)
    before = copy.deepcopy(state.read().value)
    monkeypatch.setattr(deployment_cli, "backend_settings_from_config", lambda _: settings())
    monkeypatch.setattr(
        "nebius_cxcli.deployment_local.LocalObjectStore.for_project", lambda _: state.store
    )
    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", lambda _: None)
    monkeypatch.setattr(
        cli,
        "_deployment_execution",
        lambda **kw: nullcontext(SimpleNamespace(assert_held=lambda: None)),
    )
    monkeypatch.setattr(
        deployment_cli._CliDeploymentExecutor,
        "preflight",
        lambda _: pytest.fail("generic deployment preflight reached"),
    )
    with pytest.raises(pytest.fail.Exception, match="generic deployment preflight reached"):
        deployment_cli.deploy_rendered_bundle(
            manifest["runtime_config"],
            local,
            manifest,
            options=deployment_cli.DeployOptions(dry_run=dry_run),
        )
    assert state.read().value == before
    assert jobs == []


def test_candidate_compares_existing_apps_after_immutable_source_binding(
    installed_project, monkeypatch
):
    local, initial, _, _, _, _ = installed_project
    name = "flux/targets/cluster/ordinary/source-existing.yaml"
    original = {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "OCIRepository",
        "metadata": {"name": "existing", "namespace": "flux-system"},
        "spec": {"url": "oci://example.invalid/chart", "ref": {"digest": "sha256:" + "a" * 64}},
    }
    base = DeploymentGeneration(
        initial.manifest,
        {**initial.files, name: base64.b64encode(yaml.safe_dump(original).encode()).decode()},
    )

    def render(candidate, stage, **kwargs):
        (stage.generated_dir / name).write_text(
            yaml.safe_dump({**original, "kind": "HelmRepository"})
        )
        (stage.generated_dir / name).with_name("kustomization.yaml").write_text(
            "resources: [source-existing.yaml]\n"
        )

    def refresh(candidate, stage, manifest):
        (stage.generated_dir / name).write_text(yaml.safe_dump(original))
        return {**copy.deepcopy(manifest), "runtime_config": copy.deepcopy(candidate)}

    monkeypatch.setattr(cli, "render_flux", render)
    monkeypatch.setattr(
        "nebius_cxcli.application_compatibility.refresh_application_manifest", refresh
    )
    generation = nsight_install.prepare_generation(
        cli, local, base, target_ref="cluster", settings=default_settings()
    )
    assert generation.files[name] == base.files[name]


def test_resume_before_publication_refuses_new_source_edits(installed_project, monkeypatch):
    local, _, state, jobs, _, _ = installed_project
    publish = nsight_install.publish_generation

    def interrupted(*args, **kwargs):
        raise RuntimeError("interrupted before publish")

    monkeypatch.setattr(nsight_install, "publish_generation", interrupted)
    with pytest.raises(RuntimeError, match="interrupted before publish"):
        nsight_install.install_profiling(
            local.config_path,
            target_ref="cluster",
            secret_name="nsight-streamer-auth",
            reports_path="/data/nsight-reports",
        )
    assert state.read().value["active"] is not None
    payload = yaml.safe_load(local.config_path.read_text())
    payload["client_info"]["client_name"] = "later-edit"
    local.config_path.write_text(yaml.safe_dump(payload))
    monkeypatch.setattr(nsight_install, "publish_generation", publish)
    with pytest.raises(RuntimeError, match="changed before publication"):
        nsight_install.install_profiling(
            local.config_path,
            target_ref="cluster",
            secret_name="nsight-streamer-auth",
            reports_path="/data/nsight-reports",
        )
    assert jobs == []
    assert (
        yaml.safe_load(local.config_path.read_text())["client_info"]["client_name"] == "later-edit"
    )


def test_same_install_repairs_proven_omissions_then_returns_to_healthy_noop(
    installed_project, monkeypatch
):
    from nebius_cxcli import nsight_installation

    local, _, state, jobs, _, _ = installed_project
    kwargs = dict(
        target_ref="cluster",
        secret_name="nsight-streamer-auth",
        reports_path="/data/nsight-reports",
    )
    nsight_install.install_profiling(local.config_path, **kwargs)
    before = copy.deepcopy(state.read().value["accepted"])
    assert len(jobs) == 3
    monkeypatch.setattr(
        nsight_installation,
        "observe_installation",
        lambda *a: nsight_installation.InstallationObservation(
            "repairable",
            ({"path": "/etc/profile.d/99-nsight.sh", "sha256": "a" * 64, "mode": 0o644},),
        ),
    )
    nsight_install.install_profiling(local.config_path, **kwargs)
    after = state.read().value["accepted"]
    assert after["generation"] == before["generation"] and len(jobs) == 4
    assert len(after["evidence"]["targets"]["cluster"]["profiling"]["installationRepairs"]) == 1
    assert (
        before["evidence"]["targets"]["cluster"]["jailState"]
        == after["evidence"]["targets"]["cluster"]["jailState"]
    )
    monkeypatch.setattr(
        nsight_installation,
        "observe_installation",
        lambda *a: nsight_installation.InstallationObservation("healthy"),
    )
    nsight_install.install_profiling(local.config_path, **kwargs)
    assert state.read().value["accepted"] == after and len(jobs) == 4
