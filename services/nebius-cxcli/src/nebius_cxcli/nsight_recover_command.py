"""Explicit recovery dispatch; never enters installation or deployment acceptance."""

from __future__ import annotations

import shlex
from contextlib import ExitStack

from .nsight_deployment_owner import recovery_owner
from .nsight_install import ProfilingJournal
from .nsight_mount_repair import repair_stage
from .nsight_profiling import profiling_settings
from .nsight_recovery import STAGES, expected_manifest, recover_stage
from .soperator_operation_lock import SoperatorOperationLease, SoperatorOperationLocalLock
from .terminal_styles import print_copy_paste_command


def recover_profiling(config_path, *, target_ref, stage, job_uid, dry_run=False, repair=""):
    from . import cli
    from .deployment_applications import recorded_application_identity
    from .deployment_cli import deployment_execution
    from .grafana_runtime import GRAFANA_TARGET_KUBE_CONTEXT_ENV
    from .terraform_backend import backend_settings_from_config

    stage = "nsight-" + stage
    if stage not in STAGES or not job_uid.strip():
        raise ValueError("Recovery requires admit, install or verify and an exact failed Job UID")
    if repair not in {"", "runtime-mounts", "runtime-image", "profile-order"} or (
        repair and stage != repair_stage(repair)
    ):
        raise ValueError(
            "--repair accepts runtime-mounts/runtime-image for admit or profile-order for install"
        )
    config, paths, manifest = cli._load_deploy_context_readonly(config_path)
    target = cli._resolve_selected_deploy_targets(
        manifest, requested_target_ref=target_ref, all_targets=False
    )[0]
    backend = backend_settings_from_config(config)

    def read_only():
        raise RuntimeError("Dry-run recovery cannot write deployment state")

    state, record = recovery_owner(
        paths,
        backend,
        target_ref=target_ref,
        stage=stage,
        job_uid=job_uid,
        assert_held=read_only,
    )
    active = record.value["active"]
    generation = state.generation(active["generation"])
    plan = active["plan"]
    if repair and plan.get("kind") != "nsight-profiling":
        raise RuntimeError("Runtime repair is limited to standalone profiling installation")
    identity = recorded_application_identity(record.value, paths=paths, target_ref=target_ref)
    if not identity or target_ref not in plan.get("semanticPlan", {}).get("selectedTargets", []):
        raise RuntimeError("Recovery target differs from the active operation")
    desired = generation.manifest["runtime_config"]
    settings = profiling_settings(desired, target_ref)
    if settings is None:
        raise RuntimeError("The frozen operation has no Nsight configuration")
    with ExitStack() as stack:
        backend_lease = None
        if not dry_run:
            backend_lease = stack.enter_context(
                deployment_execution(
                    config=config,
                    paths=paths,
                    target_ref=target_ref,
                    operation_id=active["generation"],
                    bootstrap_backend=False,
                )
            )
            stack.enter_context(
                SoperatorOperationLocalLock(paths.project_dir / ".nebius-cxcli" / "config.lock")
            )
            state.assert_held = backend_lease.assert_held
            fresh = state.read()
            if fresh is None or fresh.value != record.value:
                raise RuntimeError("The active deployment changed during recovery admission")
            record = fresh
        env = cli._prepare_cluster_handoff_kube_env(
            config,
            paths,
            stack=stack,
            target={**target, "cluster_id": identity["cluster_id"], "kube_context": ""},
            persist_local_kubeconfig=False,
            set_current_context=False,
            allow_terraform_output=False,
            require_renewable_auth=True,
        )
        if not env:
            raise RuntimeError("Recovery could not establish the frozen cluster handoff")
        context = env[GRAFANA_TARGET_KUBE_CONTEXT_ENV]
        if (
            cli._read_kube_system_namespace_uid(kube_context=context, extra_env=env)
            != identity["kubernetes_uid"]
        ):
            raise RuntimeError("Recovery handoff differs from the frozen cluster identity")
        lease = None
        # Upgrade uses its operation-spec fingerprint; its owner acquires the cluster lease.
        if plan.get("kind") == "nsight-profiling" and not dry_run:
            lease = stack.enter_context(
                SoperatorOperationLease(
                    kube_context=context,
                    cluster_id=identity["cluster_id"],
                    operation_fingerprint=generation.identity,
                    extra_env=env,
                )
            )

        def fence():
            if backend_lease:
                backend_lease.assert_held()
            if lease:
                lease.assert_held()
            current = state.read()
            if (
                not current
                or not current.value.get("active")
                or any(
                    current.value["active"].get(key) != active[key]
                    for key in ("generation", "plan")
                )
            ):
                raise RuntimeError("The frozen deployment owner changed")

        if plan.get("kind") == "nsight-profiling":
            _recover_install(
                cli,
                paths,
                state,
                record,
                generation=generation,
                settings=settings,
                identity=identity,
                target_ref=target_ref,
                stage=stage,
                job_uid=job_uid,
                lease=lease,
                fence=fence,
                env=env,
                dry_run=dry_run,
                repair=repair,
            )
        else:
            from .nsight_upgrade_recovery import recover_upgrade

            recover_upgrade(
                cli,
                paths,
                target=target,
                state=state,
                record=record,
                generation=generation,
                identity=identity,
                stage=stage,
                job_uid=job_uid,
                stack=stack,
                fence=fence,
                env=env,
                dry_run=dry_run,
            )


def _recover_install(
    cli,
    paths,
    state,
    record,
    *,
    generation,
    settings,
    identity,
    target_ref,
    stage,
    job_uid,
    lease,
    fence,
    env,
    dry_run,
    repair="",
):
    from .deploy_targets import flux_target_dir
    from .deployment_jail_state import jail_values, observe_jail_storage
    from .grafana_runtime import GRAFANA_TARGET_KUBE_CONTEXT_ENV

    plan = record.value["active"]["plan"]
    if (
        plan.get("target") != target_ref
        or plan.get("settings") != settings
        or plan.get("identity") != identity
    ):
        raise RuntimeError("Recovery differs from the frozen profiling owner")
    journal = ProfilingJournal(state, record)
    transition = journal.get("publish")
    if transition is None:
        raise RuntimeError("Profiling recovery requires completed configuration publication")
    evidence = record.value["accepted"]["evidence"]["targets"][target_ref]
    desired = generation.manifest["runtime_config"]
    values = jail_values(desired, target_ref)
    context = env[GRAFANA_TARGET_KUBE_CONTEXT_ENV]
    directory = flux_target_dir(paths, target_ref)

    def physical_fence():
        fence()
        if cli.project_generation_snapshot_sha256(paths) != transition.project_postimage_sha256:
            raise RuntimeError("Profiling source or rendered inputs changed")
        observed = observe_jail_storage(
            cli, flux_dir=directory, values=values, kube_context=context, kube_env=env
        )
        previous = evidence["jailState"]["storageEvidence"]
        if (
            observed["volumes"] != previous["volumes"]
            or observed["activePvcName"] != previous["activePvcName"]
        ):
            raise RuntimeError("Profiling jail storage identity changed")
        return observed

    storage = physical_fence()
    pvc = storage["activePvcName"]
    binding = storage["volumes"][pvc]
    adapter = cli._rendered_soperator_adapter_state(directory)
    request = expected_manifest(
        journal,
        repair=repair,
        stage=stage,
        operation_id=generation.identity,
        config=desired,
        target_ref=target_ref,
        image=adapter["targetImage"],
        pvc=pvc,
        pvc_uid=binding["pvcUid"],
        pv_uid=binding["pvUid"],
        filesystem_id=adapter["filesystemId"],
        generation=generation.identity,
        reports_values=values,
    )
    epoch = (
        lease.authority.fencing_epoch if lease else journal.stage(stage)["attempts"][-1]["epoch"]
    )
    recover_stage(
        cli,
        journal,
        stage=stage,
        predecessor_uid=job_uid,
        epoch=epoch,
        manifest=request,
        fence=physical_fence,
        env=env,
        dry_run=dry_run,
        repair=repair,
    )
    print_copy_paste_command(
        cli.console,
        shlex.join(
            [
                "nebius-cxcli",
                "soperator",
                "profiling",
                "install",
                str(paths.config_path),
                "--target",
                target_ref,
                "--secret-name",
                settings["secret_name"],
                "--reports-path",
                settings["reports_path"],
            ]
        ),
    )
