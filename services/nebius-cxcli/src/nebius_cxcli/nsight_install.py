"""Fenced profiling installation on one already accepted Soperator target."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import typer
import yaml

from .deployment_local import LocalObjectStore
from .deployment_state import DeploymentGeneration, DeploymentState, digest
from .nsight import (
    APP_TOOLS,
    CHART_REPOSITORY,
    REPORTS_PATH,
    soperator_report_binding,
    viewer_values,
)
from .nsight_profiling import (
    active_verify_arguments,
    customize_generation,
    default_settings,
    parse_result,
)
from .nsight_runtime import NsightKubernetes, collect_nsight_status, prepare_nsight_viewers
from .operation_config_authority import config_transition_from_payload
from .soperator_operation_lock import SoperatorOperationLease, SoperatorOperationLocalLock
from .soperator_upgrade_progress import SoperatorUpgradeProgress
from .terminal_styles import print_copy_paste_command


def candidate_config(
    base: Mapping[str, Any], *, target_ref: str, settings: Mapping[str, Any]
) -> dict[str, Any]:
    from .deployment_plan import soperator_target

    candidate = copy.deepcopy(dict(base))
    selected = soperator_target(candidate)
    if selected is None or selected[0] != target_ref:
        raise ValueError("Profiling requires the exact accepted Soperator target")
    values = selected[1].get("values", {})
    binding = soperator_report_binding(
        values, namespace="soperator", reports_path=settings["reports_path"]
    )
    targets = [
        row
        for row in candidate.get("deploy", {}).get("targets", [])
        if row.get("instance_id") == target_ref
    ]
    if len(targets) != 1:
        raise ValueError("Profiling requires one explicit deployment target")
    targets[0]["profiling"] = dict(settings)
    charts = candidate.setdefault("apps", {}).setdefault("charts", [])
    for app, tool in APP_TOOLS.items():
        matches = [
            row for row in charts if row.get("id") == app and row.get("instance_id") == target_ref
        ]
        if len(matches) > 1:
            raise ValueError("Profiling viewer is configured more than once")
        row: dict[str, Any] = matches[0] if matches else {"id": app, "instance_id": target_ref}
        if not matches:
            charts.append(row)
        old = row.get("values", {})
        new = viewer_values(
            tool, claim=binding.claim, subpath=binding.subpath, secret=settings["secret_name"]
        )
        for key in ("resources", "nodeSelector", "affinity", "tolerations", "imagePullSecrets"):
            if key in old:
                new[key] = copy.deepcopy(old[key])
        row.pop("release_name", None)
        row.update(
            {
                "enabled": True,
                "namespace": "soperator",
                "release-name": app,
                "values": new,
                "version": "2026.4.1",
                "repo": CHART_REPOSITORY,
            }
        )
    return candidate


class ProfilingJournal:
    def __init__(self, state, record):
        self.state, self.current = state, record

    def stage(self, name):
        return self.current.value["active"]["stages"].get(name)

    def checkpoint(self, name, evidence):
        if name.startswith("nsight-"):
            from .nsight_recovery import STAGES, validate_transition

            if name not in STAGES:
                raise ValueError("Unsupported Nsight stage")
            prior = self.stage(name)
            if prior == evidence:
                return
            if any(self.stage(later) is not None for later in STAGES[STAGES.index(name) + 1 :]):
                raise RuntimeError("Nsight recovery cannot rewind later stages")
            validate_transition(prior, evidence)
        self.current = self.state.checkpoint(self.current, stage=name, evidence=evidence)

    def get(self, stage):
        row = self.stage("config:" + stage)
        return config_transition_from_payload(row) if row else None

    def record(self, transition):
        self.checkpoint("config:" + transition.stage, asdict(transition))


def verify_active_tools(env, receipt):
    from .nsight_profiling import validate_customization

    body = {key: value for key, value in receipt.items() if key != "receiptSha256"}
    if receipt.get("receiptSha256") != digest(body):
        raise RuntimeError("Accepted profiling receipt changed")
    validate_customization(receipt["verification"], receipt["admission"])
    verified = parse_result(
        NsightKubernetes(env).run(
            [
                "-n",
                "soperator",
                "exec",
                "login-0",
                "--",
                *active_verify_arguments(receipt["admission"]),
            ],
            timeout=900,
        )
    )
    if verified != receipt["verification"]:
        raise RuntimeError("The active login jail differs from the Nsight customization receipt")


def finish_installation(cli, paths, generation, *, target_ref, identity, statuses, reports_path):
    from .ordinary_apps import accept_ordinary_app_baseline

    if not accept_ordinary_app_baseline(
        paths,
        identities={target_ref: identity},
        selected_target_refs=(target_ref,),
        deployment_generation=generation.identity,
        expected_generation=generation,
    ):
        raise RuntimeError(
            "Profiling installed but local accepted app baseline requires recovery; rerun the same command"
        )
    cli.console.print(
        "Nsight Systems 2026.4.1 and Nsight Compute 2026.2.1 are installed in the shared jail."
    )
    cli.console.print("For an already-open login shell:")
    print_copy_paste_command(cli.console, "source /etc/profile.d/99-nsight.sh")
    cli.console.print(
        f"Write reports under {reports_path}; open them from /mnt/reports in each viewer."
    )
    for status in statuses:
        if status.get("port_forward_command"):
            print_copy_paste_command(cli.console, status["port_forward_command"])
            cli.console.print(
                f"Open {status['url']} — keep each port-forward running in its own terminal."
            )
        else:
            cli.console.print("Viewer installed; persistent kubeconfig access setup is incomplete.")
    password_commands = dict.fromkeys(
        status["password_command"] for status in statuses if status.get("password_command")
    )
    for command in password_commands:
        cli.console.print("To display your Nsight browser password, run:")
        print_copy_paste_command(cli.console, command)


def prepare_generation(cli, paths, base, *, target_ref, settings):
    from .application_compatibility import refresh_application_manifest
    from .config_model import to_dynamic_payload
    from .deploy_targets import flux_target_dir
    from .generated_manifest import manifest_path_for_generated_dir
    from .ordinary_apps import (
        omit_shared_protected_resources,
        resource_documents,
        resource_identity,
    )

    candidate = candidate_config(
        base.manifest["runtime_config"], target_ref=target_ref, settings=settings
    )
    stage = cli.staged_generated_paths(paths)
    try:
        stage.generated_dir.rmdir()
        manifest = base.materialize(stage)
        before = {
            resource_identity(row): row
            for row in resource_documents(flux_target_dir(stage, target_ref) / "ordinary")
        }
        cli.render_flux(candidate, stage, ordinary_only=True)
        for target in manifest.get("deploy", {}).get("targets", []):
            directory = flux_target_dir(stage, target["target_ref"])
            omit_shared_protected_resources(directory / "ordinary", directory)
        # Compare the final digest-bound resources, including existing ordinary
        # OCI sources, rather than the renderer's provisional HelmRepository.
        manifest = refresh_application_manifest(candidate, stage, manifest)
        after = {
            resource_identity(row): row
            for row in resource_documents(flux_target_dir(stage, target_ref) / "ordinary")
        }
        viewers = set(APP_TOOLS)
        for identity, doc in before.items():
            if doc.get("kind") == "HelmRelease" and doc.get("metadata", {}).get("name") in viewers:
                continue
            if after.get(identity) != doc:
                raise RuntimeError(
                    "Profiling render would change another App; reconcile it separately first"
                )
        source = cli.render_updated_source_payload(to_dynamic_payload(candidate))
        manifest["render"]["source_config_sha256"] = digest(yaml.safe_load(source))
        manifest["render"]["app_scope"] = "ordinary"
        manifest["render"]["ordinary_files"] = {
            p.relative_to(stage.generated_dir).as_posix(): "sha256:"
            + hashlib.sha256(p.read_bytes()).hexdigest()
            for p in stage.flux_dir.rglob("ordinary/*")
            if p.is_file()
        }
        manifest_path_for_generated_dir(stage.generated_dir).write_text(json.dumps(manifest))
        result = DeploymentGeneration.capture(stage, manifest)
        # No protected chart, Terraform input or other target byte may change.
        target_root = (
            flux_target_dir(stage, target_ref).relative_to(stage.generated_dir).as_posix()
            + "/ordinary/"
        )
        if {k: v for k, v in base.files.items() if not k.startswith(target_root)} != {
            k: v for k, v in result.files.items() if not k.startswith(target_root)
        }:
            raise RuntimeError("Profiling render changed files outside its target's ordinary Apps")
        return result
    finally:
        cli.reset_generated_bundle(stage)


def publish_generation(cli, paths, generation, *, journal, fence):
    from .config_model import to_dynamic_payload
    from .generated_manifest import manifest_path_for_generated_dir
    from .operation_config_authority import apply_project_generation_transition

    stage = cli.staged_generated_paths(paths)
    try:
        stage.generated_dir.rmdir()
        generation.materialize(stage)
        manifest_path_for_generated_dir(stage.generated_dir).write_text(
            json.dumps(generation.manifest_for_paths(paths), sort_keys=True) + "\n"
        )
        plan = cli.build_project_generation_plan(
            final_paths=paths,
            staged_paths=stage,
            config_path=paths.config_path,
            config_content=cli.render_updated_source_payload(
                to_dynamic_payload(generation.manifest["runtime_config"])
            ),
        )
        return apply_project_generation_transition(
            project_dir=paths.project_dir,
            config_path=paths.config_path,
            owner="soperator-profiling",
            stage="publish",
            store=journal,
            build_plan=lambda: plan,
            assert_authority=fence,
            current_project_snapshot_sha256=lambda: cli.project_generation_snapshot_sha256(paths),
        )
    finally:
        cli.reset_generated_bundle(stage)


def apply_viewers(cli, paths, config, *, target_ref, env, fence):
    """Apply the explicit viewer/resource slice through the ordinary Flux owner."""
    from .deploy_targets import flux_target_dir
    from .ordinary_apps import (
        resource_documents,
        runtime_manifest_guard,
        validate_live_app_ownership,
    )

    docs = resource_documents(flux_target_dir(paths, target_ref) / "ordinary")
    releases = [
        row
        for row in docs
        if row.get("kind") == "HelmRelease" and row.get("metadata", {}).get("name") in APP_TOOLS
    ]
    if len(releases) != 2:
        raise RuntimeError("Profiling requires the two exact rendered viewer releases")
    repositories = {r["spec"]["chart"]["spec"]["sourceRef"]["name"] for r in releases}
    selected = [
        row
        for row in docs
        if row in releases
        or (row.get("kind") == "HelmRepository" and row["metadata"]["name"] in repositories)
    ]
    with tempfile.TemporaryDirectory(prefix="cxcli-nsight-apps-") as directory:
        root = Path(directory)
        (root / "viewers.yaml").write_text(yaml.safe_dump_all(selected, sort_keys=False))
        (root / "kustomization.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "kustomize.config.k8s.io/v1beta1",
                    "kind": "Kustomization",
                    "resources": ["viewers.yaml"],
                }
            )
        )
        validate_live_app_ownership(root, extra_env=env)
        owner = hashlib.sha256(
            json.dumps([config.get("client_info", {}), target_ref], sort_keys=True).encode()
        ).hexdigest()
        with cli.app_mutation_scope(fence, runtime_manifest_guard(owner, env)):
            fence()
            charts = [
                row
                for row in config["apps"]["charts"]
                if row.get("id") in APP_TOOLS and row.get("instance_id") == target_ref
            ]
            cli._apply_rendered_flux(
                replace(paths, flux_dir=root),
                config={**config, "apps": {"charts": charts}},
                require_existing_flux=True,
                extra_env=env,
                target_ref=target_ref,
                assert_authority=fence,
            )


def install_profiling(
    config_path: Path,
    *,
    target_ref: str,
    secret_name: str,
    reports_path: str,
    username: str = "admin",
    interactive: bool | None = None,
    password_stdin: bool = False,
) -> None:
    from . import cli
    from .deploy_targets import flux_target_dir
    from .deployment_applications import target_bundle_digest
    from .deployment_cli import deployment_execution
    from .deployment_jail_state import (
        accepted_effective_generation,
        build_jail_state_receipt,
        jail_values,
        observe_jail_storage,
    )
    from .generated_manifest import source_config_digest
    from .grafana_runtime import GRAFANA_TARGET_KUBE_CONTEXT_ENV
    from .ordinary_apps import validate_ordinary_app_scope
    from .soperator_protected_data_plane import bind_protected_job_authority
    from .terraform_backend import backend_settings_from_config

    if interactive is None:
        interactive = not password_stdin
    if interactive and password_stdin:
        raise ValueError("Choose --interactive or --password-stdin, not both")
    progress = SoperatorUpgradeProgress(cli.progress_console, prefix="Nsight")
    with progress.phase("nsight-config", "Load profiling configuration and target"):
        config, paths, manifest = cli._load_deploy_context_readonly(config_path)
        target = cli._resolve_selected_deploy_targets(
            manifest, requested_target_ref=target_ref, all_targets=False
        )[0]
        settings = default_settings(secret_name=secret_name, reports_path=reports_path)
    with ExitStack() as stack:
        with progress.phase("nsight-project-lock", "Acquire profiling project locks"):
            backend_lease = stack.enter_context(
                deployment_execution(
                    config=config,
                    paths=paths,
                    target_ref=target_ref,
                    operation_id=digest([target_ref, settings]),
                    bootstrap_backend=False,
                )
            )
            stack.enter_context(
                SoperatorOperationLocalLock(paths.project_dir / ".nebius-cxcli" / "config.lock")
            )
        with progress.phase(
            "nsight-accepted-state", "Check accepted deployment and shared jail identity"
        ):
            backend = backend_settings_from_config(config)
            state = DeploymentState(
                LocalObjectStore.for_project(paths),
                backend,
                command="profiling-" + target_ref,
                baseline_targets=(target_ref,),
                assert_held=backend_lease.assert_held,
            )
            record = state.read()
            current = None
            if not record or not record.value.get("active"):
                current = DeploymentGeneration.capture(paths, manifest)
                state.baseline_generation = current.identity
                record = state.read()
            accepted = record.value.get("accepted") if record else None
            if not accepted:
                raise RuntimeError("Profiling requires a completed, accepted Soperator deployment")
            evidence = accepted["evidence"]["targets"].get(target_ref)
            if not evidence or not evidence.get("jailState"):
                raise RuntimeError("Profiling target has no accepted jail identity")
            effective = accepted_effective_generation(state, target_ref, evidence)
            identity = evidence["identity"]
            assert effective is not None and record is not None
        from .installation_reconciliation import (
            pending_reconciliation,
            reconcile_accepted_installation,
        )

        active = record.value.get("active")
        if pending_reconciliation(record.value, target_ref):
            active = None  # Resume metadata authority after acquiring the cluster fence.
        already_installed = False
        if active:
            plan = active["plan"]
            if (
                plan.get("kind") != "nsight-profiling"
                or plan.get("target") != target_ref
                or plan.get("settings") != settings
                or plan.get("identity") != identity
            ):
                raise RuntimeError(
                    "A different deployment is active; finish it before profiling installation"
                )
            generation = state.generation(active["generation"])
            already_installed = bool(plan.get("repair"))
            if already_installed and plan["repair"].get("receipt") != evidence.get("profiling"):
                raise RuntimeError(
                    "Installation repair differs from the accepted profiling receipt"
                )
        else:
            if current is None:
                current = DeploymentGeneration.capture(paths, manifest)
            if source_config_digest(paths.config_path) != manifest["render"][
                "source_config_sha256"
            ] or current.identity not in {accepted["generation"], effective.identity}:
                raise RuntimeError(
                    "Profiling refuses pending source or rendered changes; complete them separately first"
                )
            if jail_values(current.manifest["runtime_config"], target_ref) != jail_values(
                effective.manifest["runtime_config"], target_ref
            ):
                raise RuntimeError(
                    "Profiling requires the locally published accepted jail generation"
                )
            prior_profile = evidence.get("profiling", {})
            already_installed = prior_profile.get(
                "generation"
            ) == current.identity and prior_profile.get("settingsSha256") == digest(settings)
            if already_installed:
                generation = current
            else:
                validate_ordinary_app_scope(paths, manifest=manifest)
                with progress.phase(
                    "nsight-render", "Prepare and render both pinned Nsight viewers"
                ):
                    generation = prepare_generation(
                        cli, paths, current, target_ref=target_ref, settings=settings
                    )
            plan = {
                "kind": "nsight-profiling",
                "target": target_ref,
                "settings": settings,
                "identity": identity,
                "sourceGeneration": current.identity,
                "sourceSnapshot": cli.project_generation_snapshot_sha256(paths),
                "semanticPlan": {"selectedTargets": [target_ref]},
            }
        with progress.phase(
            "nsight-handoff", "Connect to the accepted cluster and verify its identity"
        ):
            env = cli._prepare_cluster_handoff_kube_env(
                config,
                paths,
                stack=stack,
                target={**target, "cluster_id": identity["cluster_id"], "kube_context": ""},
                persist_local_kubeconfig=True,
                set_current_context=False,
                allow_terraform_output=False,
                require_renewable_auth=True,
            )
            if not env:
                raise RuntimeError("Profiling could not establish its accepted cluster handoff")
            context = env[GRAFANA_TARGET_KUBE_CONTEXT_ENV]
            if (
                cli._read_kube_system_namespace_uid(kube_context=context, extra_env=env)
                != identity["kubernetes_uid"]
            ):
                raise RuntimeError("Profiling handoff differs from the accepted cluster identity")
        with progress.phase("nsight-cluster-lock", "Acquire the Soperator operation lock"):
            lease = stack.enter_context(
                SoperatorOperationLease(
                    kube_context=context,
                    cluster_id=identity["cluster_id"],
                    operation_fingerprint=generation.identity,
                    extra_env=env,
                )
            )

        def lease_fence():
            backend_lease.assert_held()
            lease.assert_held()

        state.assert_held = lease_fence
        if not active:
            with progress.phase("nsight-reconcile", "Check accepted installation records"):
                record = reconcile_accepted_installation(
                    cli, state, target_ref=target_ref, env=env, fence=lease_fence
                )
        desired = generation.manifest["runtime_config"]
        with progress.phase("nsight-storage", "Verify shared jail storage and unchanged inputs"):
            storage = observe_jail_storage(
                cli,
                flux_dir=flux_target_dir(paths, target_ref),
                values=jail_values(effective.manifest["runtime_config"], target_ref),
                kube_context=context,
                kube_env=env,
            )
            previous_volumes = evidence["jailState"]["storageEvidence"].get("volumes", {})
            if storage["volumes"] != previous_volumes:
                raise RuntimeError("Profiling storage identities differ from the accepted jail")
            if (not active or already_installed) and cli.project_generation_snapshot_sha256(
                paths
            ) != plan["sourceSnapshot"]:
                raise RuntimeError("Profiling inputs changed during admission")
        from .nsight_credentials import prepare_login_credentials

        prepare_login_credentials(
            env=env,
            name=secret_name,
            username=username,
            interactive=interactive,
            password_stdin=password_stdin,
            fence=lease_fence,
            progress=progress,
        )
        with progress.phase(
            "nsight-viewer-preflight", "Check viewer credentials and report storage"
        ):
            prepare_nsight_viewers(desired, extra_env=env, target_ref=target_ref)
        if already_installed:

            def accepted_fence():
                lease_fence()
                if cli.project_generation_snapshot_sha256(paths) != plan["sourceSnapshot"]:
                    raise RuntimeError("Accepted profiling inputs changed during verification")

            accepted_fence()
            receipt = evidence["profiling"]
            active_binding = storage["volumes"][storage["activePvcName"]]
            if (
                receipt["pvcUid"] != active_binding["pvcUid"]
                or receipt["pvUid"] != active_binding["pvUid"]
            ):
                raise RuntimeError("Accepted profiling receipt belongs to another jail generation")
            from .nsight_installation import observe_installation, restore_installation

            if not plan.get("repair"):
                with progress.phase(
                    "nsight-installed-tools", "Verify the accepted profiler installation"
                ):
                    observation = observe_installation(env, receipt, verify_active_tools)
                if observation.state == "repairable":
                    plan = {
                        **plan,
                        "repair": {"receipt": receipt, "omissions": list(observation.omissions)},
                    }
                    accepted_fence()
                    record = state.begin(generation, plan=plan)
            repair_journal = None
            if plan.get("repair"):
                repair_journal = ProfilingJournal(state, record)
                with progress.phase(
                    "nsight-repair", "Restore and verify missing managed profiler files"
                ):
                    receipt = restore_installation(
                        cli,
                        repair_journal,
                        plan=plan,
                        adapter=cli._rendered_soperator_adapter_state(
                            flux_target_dir(paths, target_ref)
                        ),
                        storage=storage,
                        env=env,
                        fence=accepted_fence,
                        epoch=lease.authority.fencing_epoch,
                    )
                    verify_active_tools(env, receipt)
            progress.message("Nsight: Deploy and wait for both browser viewers")
            apply_viewers(cli, paths, desired, target_ref=target_ref, env=env, fence=accepted_fence)
            with progress.phase(
                "nsight-access", "Verify viewer readiness and prepare private access commands"
            ):
                statuses = collect_nsight_status(desired, extra_env=env, target_ref=target_ref)
            accepted_fence()
            if repair_journal is not None:
                with progress.phase(
                    "nsight-accept-repair", "Verify storage and accept the repaired installation"
                ):
                    final_storage = observe_jail_storage(
                        cli,
                        flux_dir=flux_target_dir(paths, target_ref),
                        values=jail_values(effective.manifest["runtime_config"], target_ref),
                        kube_context=context,
                        kube_env=env,
                    )
                    if final_storage != storage:
                        raise RuntimeError("Shared jail storage changed during installation repair")
                    accepted_fence()
                    state.accept(
                        repair_journal.current,
                        evidence={
                            "identities": {target_ref: identity},
                            "targets": {target_ref: {**evidence, "profiling": receipt}},
                        },
                        derived_generations=(effective,),
                    )
                    reconcile_accepted_installation(
                        cli, state, target_ref=target_ref, env=env, fence=accepted_fence
                    )
            finish_installation(
                cli,
                paths,
                generation,
                target_ref=target_ref,
                identity=identity,
                statuses=statuses,
                reports_path=reports_path,
            )
            return
        with progress.phase(
            "nsight-publish", "Publish profiling configuration and rendered viewers"
        ):
            record = state.begin(generation, plan=plan)
            journal = ProfilingJournal(state, record)
            if (
                journal.get("publish") is None
                and cli.project_generation_snapshot_sha256(paths) != plan["sourceSnapshot"]
            ):
                raise RuntimeError(
                    "Profiling inputs changed before publication; restore the frozen inputs"
                )
            transition = publish_generation(
                cli, paths, generation, journal=journal, fence=lease_fence
            )

        def fence():
            lease_fence()
            if cli.project_generation_snapshot_sha256(paths) != transition.project_postimage_sha256:
                raise RuntimeError("Profiling config or generated inputs changed during execution")

        pvc = storage["activePvcName"]
        binding = storage["volumes"][pvc]
        adapter = cli._rendered_soperator_adapter_state(flux_target_dir(paths, target_ref))

        def run_job(stage_name, request):
            from .nsight_recovery import run_attempt

            job = bind_protected_job_authority(
                request,
                operation_id=generation.identity,
                fence_epoch=lease.authority.fencing_epoch,
                pvc_uid=binding["pvcUid"],
            )
            descriptions = {
                "nsight-admit": "Check shared jail prerequisites and resolve profiler packages",
                "nsight-install": "Install Nsight Systems and Compute in the shared jail",
                "nsight-verify": "Verify shared jail profiler versions and activation",
            }
            with progress.phase(stage_name, descriptions[stage_name]):
                return run_attempt(
                    cli,
                    journal,
                    stage=stage_name,
                    manifest=job,
                    fence=fence,
                    context=context,
                    env=env,
                    retry_epoch=lease.authority.fencing_epoch,
                )

        receipt = customize_generation(
            config=desired,
            target_ref=target_ref,
            image=adapter["targetImage"],
            pvc=pvc,
            pvc_uid=binding["pvcUid"],
            pv_uid=binding["pvUid"],
            filesystem_id=adapter["filesystemId"],
            generation=generation.identity,
            run_job=run_job,
            reports_values=jail_values(desired, target_ref),
        )
        assert receipt is not None
        from .nsight_recovery import bind_attempt_receipt

        receipt = bind_attempt_receipt(receipt, journal)
        fence()
        # Fresh verification through the actual active login jail also runs after
        # recovery; a historical successful Job is not current executable proof.
        with progress.phase(
            "nsight-active-tools", "Verify nsys and ncu through the active login node"
        ):
            verify_active_tools(env, receipt)
            journal.checkpoint("profiling", receipt)
        progress.message("Nsight: Deploy and wait for both browser viewers")
        apply_viewers(cli, paths, desired, target_ref=target_ref, env=env, fence=fence)
        with progress.phase(
            "nsight-access", "Verify viewer readiness and prepare private access commands"
        ):
            statuses = collect_nsight_status(desired, extra_env=env, target_ref=target_ref)
        with progress.phase(
            "nsight-accept", "Verify shared storage and record installation acceptance"
        ):
            fence()
            final_storage = observe_jail_storage(
                cli,
                flux_dir=flux_target_dir(paths, target_ref),
                values=jail_values(desired, target_ref),
                kube_context=context,
                kube_env=env,
            )
            if final_storage != storage:
                raise RuntimeError("Shared jail storage changed during profiling installation")
            jail_receipt = build_jail_state_receipt(
                authored=generation,
                effective=generation,
                target_ref=target_ref,
                identity=identity,
                storage_evidence=final_storage,
            )
            proof = {
                **evidence,
                "identity": identity,
                "generation": generation.identity,
                "effectiveGeneration": generation.identity,
                "desiredBundle": target_bundle_digest(generation, target_ref),
                "jailState": jail_receipt,
                "profiling": receipt,
            }
            state.accept(
                journal.current,
                evidence={"identities": {target_ref: identity}, "targets": {target_ref: proof}},
                derived_generations=(generation,),
            )
            reconcile_accepted_installation(
                cli, state, target_ref=target_ref, env=env, fence=lease_fence
            )
        finish_installation(
            cli,
            paths,
            generation,
            target_ref=target_ref,
            identity=identity,
            statuses=statuses,
            reports_path=reports_path,
        )


profiling_app = typer.Typer(
    help="Install shared nsys/ncu tools, show live browser access, and recover profiling on an accepted Soperator target."
)


@profiling_app.command(
    "show",
    short_help="Verify live viewers and print three browser access commands.",
    epilog="Examples: nebius-cxcli soperator profiling show ./config.yaml --target TARGET",
)
def profiling_show(
    config: Path = typer.Argument(..., metavar="CONFIG", help="Managed project config.yaml."),
    target: str = typer.Option(..., "--target", help="Exact accepted Soperator deployment target."),
) -> None:
    """Verify both live viewers and print two forwards and one shared-password command.

    Refresh local kubeconfig when enabled, preserving its current context. Commands
    use live Services and Secret references, never saved reports or pending viewer
    settings. Requires cluster and Secret-read access. Does not run the commands,
    retrieve password values, probe inside Pods or change Kubernetes resources.
    """
    from . import cli
    from .nsight_access import show_nsight_access

    try:
        access = show_nsight_access(config.expanduser().resolve(), target)
        for line in access.terminal_lines():
            if line.startswith("#"):
                cli.console.print(line, markup=False, highlight=False, soft_wrap=True)
            else:
                print_copy_paste_command(cli.console, line)
    except (OSError, subprocess.SubprocessError):
        cli.console.print(
            "Error: Unable to verify profiling access; check connectivity and permissions.",
            markup=False,
        )
        raise typer.Exit(1) from None
    except (KeyError, TypeError, AttributeError):
        cli.console.print("Error: Live profiling access evidence is incomplete.", markup=False)
        raise typer.Exit(1) from None
    except (ValueError, RuntimeError) as exc:
        cli.console.print(f"Error: {exc}", markup=False, highlight=False)
        raise typer.Exit(1) from None


@profiling_app.command(
    "install",
    short_help="Install both Nsight CLIs and browser viewers.",
    help=(
        "Install both Nsight CLIs and browser viewers on an accepted Soperator target. "
        "The login wizard runs by default for a missing Secret, asking for a username "
        "(default admin) and a nonblank, masked, confirmed password. Existing credentials "
        "are reused without prompting. Use --no-interactive to require an existing Secret "
        "or --password-stdin for automation. Success prints a command you can run to "
        "retrieve the password; cxcli never prints the password itself. "
        "Rerun with the same options to reuse healthy stages, resume safe terminal failures, "
        "repair proven managed-file omissions and reconcile accepted installation records."
    ),
    epilog="Examples: nebius-cxcli soperator profiling install <config.yaml> --target <target>",
)
def profiling_install(
    config: Path = typer.Argument(..., metavar="CONFIG", help="Accepted project config.yaml."),
    target: str = typer.Option(..., "--target", help="Exact accepted Soperator deployment target."),
    secret_name: str = typer.Option(
        "nsight-streamer-auth",
        "--secret-name",
        help="Login Secret in soperator; reused or created from explicit runtime credential input.",
    ),
    reports_path: str = typer.Option(
        REPORTS_PATH,
        "--reports-path",
        help="Reports directory inside a persistent shared jail submount.",
    ),
    username: str = typer.Option("admin", "--username", help="Username for a new login Secret."),
    interactive: bool | None = typer.Option(
        None,
        "--interactive/--no-interactive",
        help="Run the login wizard for a missing Secret; disable to require an existing Secret.",
        show_default="wizard unless --password-stdin",
    ),
    password_stdin: bool = typer.Option(
        False,
        "--password-stdin",
        help="Read the login password from stdin instead of the wizard; never pass it as an argument.",
    ),
) -> None:
    try:
        install_profiling(
            config,
            target_ref=target,
            secret_name=secret_name,
            reports_path=reports_path,
            username=username,
            interactive=interactive,
            password_stdin=password_stdin,
        )
    except (ValueError, RuntimeError) as exc:
        from . import cli

        cli.console.print(f"[red]Profiling installation stopped:[/red] {exc}")
        raise typer.Exit(1) from exc


@profiling_app.command(
    "recover",
    short_help="Recover one exact failed Nsight Job under its original operation.",
    epilog="Examples: nebius-cxcli soperator profiling recover <config.yaml> --target <target> --stage install --job-uid <failed-job-uid> --dry-run",
)
def profiling_recover(
    config: Path = typer.Argument(..., metavar="CONFIG", help="Frozen project config.yaml."),
    target: str = typer.Option(..., "--target", help="Exact active deployment target."),
    stage: str = typer.Option(..., "--stage", help="Logical stage: admit, install, or verify."),
    job_uid: str = typer.Option(
        ..., "--job-uid", help="Exact terminal failed predecessor Job UID."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Read-only recovery admission; reserve and change nothing."
    ),
    repair: str = typer.Option(
        "",
        "--repair",
        help="Explicit repair: runtime-mounts/runtime-image for admit, profile-order for install. No arbitrary patches.",
    ),
) -> None:
    from .nsight_recover_command import recover_profiling

    try:
        recover_profiling(
            config, target_ref=target, stage=stage, job_uid=job_uid, dry_run=dry_run, repair=repair
        )
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        from . import cli

        # Key/type errors may contain malformed external payloads; do not echo those.
        detail = (
            str(exc)
            if isinstance(exc, (ValueError, RuntimeError))
            else "Frozen recovery evidence is incomplete"
        )
        cli.console.print(f"[red]Profiling recovery stopped:[/red] {detail}")
        raise typer.Exit(1) from exc
