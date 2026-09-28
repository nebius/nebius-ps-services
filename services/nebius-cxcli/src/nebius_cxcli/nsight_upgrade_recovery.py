"""Nsight recovery under the existing, pre-promotion upgrade owner."""

from __future__ import annotations

import hashlib
import json
import shlex
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from .nsight_recovery import RootfsStages, expected_manifest, recover_stage, validate_chain
from .nsight_runtime import NsightKubernetes
from .soperator_operation import (
    SoperatorOperationAnchor,
    SoperatorOperationSpec,
    load_active_soperator_release_intent,
    load_local_active_soperator_release_intent,
    soperator_sha256,
)
from .soperator_operation_lock import SoperatorLeaseAuthority, SoperatorOperationLease
from .soperator_receipt_io import read_owner_only_json
from .soperator_recovery_journal import SoperatorRecoveryIdentity, SoperatorRecoveryJournal


def deploy_resume_command(config_path, controls):
    from .deployment_cli import DeployOptions

    if not isinstance(controls, dict) or set(controls) != set(DeployOptions().controls()):
        raise RuntimeError(
            "Original deploy controls are missing; recovery cannot invent a continuation command"
        )
    if controls["targetRef"] and controls["allTargets"]:
        raise RuntimeError("Frozen deploy target selection is ambiguous")
    args = ["nebius-cxcli", "deploy", str(config_path)]
    if controls["allTargets"]:
        args += ["--all-targets"]
    elif controls["targetRef"]:
        args += ["--target", controls["targetRef"]]
    for key, flag in (
        ("jobPolicy", "--job-policy"),
        ("jobWaitTimeout", "--job-wait-timeout"),
        ("jobRefreshInterval", "--job-refresh-interval"),
    ):
        if not isinstance(controls[key], str) or not controls[key]:
            raise RuntimeError("Frozen deploy controls are incomplete")
        args += [flag, controls[key]]
    for key, flag in (
        ("cancelJobIds", "--cancel-job"),
        ("requeueJobIds", "--requeue-job"),
        ("skipValidationKinds", "--skip-validation"),
    ):
        if not isinstance(controls[key], list) or any(
            not isinstance(v, str) for v in controls[key]
        ):
            raise RuntimeError("Frozen deploy controls are malformed")
        for value in controls[key]:
            args += [flag, value]
    if controls["skipValidations"]:
        args.append("--skip-validations")
    return shlex.join(args)


def require_pre_promotion(receipt, snapshot):
    if (
        receipt.get("irreversibleIntent") is not None
        or receipt.get("irreversibleFrontier") is not None
    ):
        raise RuntimeError("Nsight recovery is blocked after promotion intent")
    transitions = receipt.get("transitions")
    if not isinstance(transitions, list) or not transitions:
        raise RuntimeError("Upgrade transition frontier is unavailable")
    # Only the currently interrupted passive-rootfs owner may be resumed.
    if transitions[-1].get("phase") != "populate-passive-jail-rootfs" or transitions[-1].get(
        "status"
    ) not in {"running", "failed"}:
        raise RuntimeError("Upgrade is outside the passive rootfs customization frontier")
    if snapshot.get("status") != "active" or "rootfs-nsight-customization" in snapshot.get(
        "stages", {}
    ):
        raise RuntimeError("Nsight upgrade customization is already sealed or published")


def recover_upgrade(
    cli,
    paths,
    *,
    target,
    state,
    record,
    generation,
    identity,
    stage,
    job_uid,
    stack,
    fence,
    env,
    dry_run,
):
    from .deployment_cli import _execution_paths
    from .deployment_jail_state import observe_protected_directories
    from .deployment_recovery import restore_execution_cache
    from .soperator_jail_protection import rootfs_storage_authority, verify_target_volume
    from .soperator_protected_data_plane import (
        protected_job_pod_identity,
        verify_passive_rootfs_consumers,
    )

    target_ref = target["target_ref"]
    original_paths = paths
    outer_snapshot = cli.project_generation_snapshot_sha256(original_paths)
    directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="cxcli-nsight-recovery-"))
    paths = _execution_paths(original_paths, Path(directory))
    generation.materialize(paths)
    recovery = record.value["active"].get("recovery")
    if not recovery:
        raise RuntimeError("Upgrade recovery requires its authenticated execution archive")
    restore_execution_cache(paths.repo_root, recovery)
    from .deploy_targets import flux_target_dir

    local = replace(paths, flux_dir=flux_target_dir(paths, target_ref))
    loaded = load_local_active_soperator_release_intent(paths=local, target_ref=target_ref)
    if loaded is None:
        raise RuntimeError("Upgrade recovery requires its exact active release intent")
    intent, _ = loaded
    admission_path = cli._soperator_upgrade_admission_path(local, target_ref)
    admission = read_owner_only_json(admission_path, label="Nsight upgrade admission")
    receipt_pair = cli._soperator_upgrade_reconcile_receipt_for_operation(
        paths=local,
        target_ref=target_ref,
        expected_operation_spec_sha256=intent.operation_spec_sha256,
        expected_intervention_generation=admission.get("interventionGeneration", 0),
    )
    if receipt_pair is None:
        raise RuntimeError("Upgrade recovery requires its original reconciliation receipt")
    receipt_path, receipt = receipt_pair
    spec = SoperatorOperationSpec(**receipt["operation"]["spec"])
    operation_id = soperator_sha256(asdict(spec))
    if (
        operation_id != intent.operation_spec_sha256
        or spec.nebius_cluster_id != identity["cluster_id"]
        or spec.kubernetes_uid != identity["kubernetes_uid"]
        or spec.target_ref != target_ref
        or soperator_sha256(admission) != spec.admission_sha256
    ):
        raise RuntimeError("Upgrade recovery immutable operation identity changed")
    preflight = cli.SoperatorRootfsAdmissionPreflight.from_payload(admission["rootfsPreflight"])
    if (
        preflight.target_image != spec.target_jail_image
        or preflight.target_pvc_name == preflight.live_pvc_name
    ):
        raise RuntimeError("Recovery target is not the admitted passive jail")
    command = deploy_resume_command(
        original_paths.config_path, record.value["active"]["plan"].get("controls")
    )
    kube = NsightKubernetes(env)
    # Read actual persisted authority; this object is used only to validate the existing anchor.
    anchor_name = f"nebius-cxcli-soperator-op-{hashlib.sha256(spec.nebius_cluster_id.encode()).hexdigest()[:10]}-{operation_id.removeprefix('sha256:')[:10]}"
    anchor_data = kube.get("configmap", anchor_name, "kube-system")["data"]
    observed_authority = SoperatorLeaseAuthority(
        lease_name=anchor_data["leaseName"],
        lease_uid=anchor_data["leaseUid"],
        holder_identity_sha256=anchor_data["holderIdentitySha256"],
        fencing_epoch=int(anchor_data["fencingEpoch"]),
        operation_fingerprint=operation_id,
    )
    anchor = SoperatorOperationAnchor(
        kube_context=kube.context,
        cluster_id=spec.nebius_cluster_id,
        operation_spec=spec,
        lease_authority=observed_authority,
        extra_env=env,
    )
    if any(anchor_data.get(k) != v for k, v in anchor._expected_data().items()):
        raise RuntimeError("Upgrade recovery requires its exact active cluster anchor")

    def runner(args, **kwargs):
        return cli._run_soperator_upgrade_process(args, extra_env=env, **kwargs)

    def make_journal(authority, guard):
        return SoperatorRecoveryJournal(
            runner=runner,
            kube_context=kube.context,
            identity=SoperatorRecoveryIdentity(
                operation_id=operation_id,
                cluster_id=spec.nebius_cluster_id,
                kubernetes_uid=spec.kubernetes_uid,
                lease_uid=authority.lease_uid,
                fencing_epoch=authority.fencing_epoch,
                source_release=spec.current_release,
                target_release=spec.target_release,
                target_jail_image=spec.target_jail_image,
                infrastructure_receipt_sha256=spec.infrastructure_plan_sha256,
                rootfs_classification_sha256=preflight.receipt_sha256,
            ),
            local_path=local.reports_dir
            / f"soperator-recovery-{operation_id.removeprefix('sha256:')[:20]}.json",
            assert_authority=guard,
        )

    journal = make_journal(observed_authority, fence)
    original = journal.snapshot()
    require_pre_promotion(receipt, original)
    source_snapshot = cli.project_generation_snapshot_sha256(paths)
    adapter = cli._rendered_soperator_adapter_state(local.flux_dir)
    stages = RootfsStages(journal)

    def physical_fence():
        fence()
        if (
            cli.project_generation_snapshot_sha256(paths) != source_snapshot
            or cli.project_generation_snapshot_sha256(original_paths) != outer_snapshot
            or read_owner_only_json(admission_path, label="Nsight upgrade admission") != admission
            or read_owner_only_json(receipt_path, label="Nsight upgrade reconciliation") != receipt
        ):
            raise RuntimeError("Upgrade recovery inputs changed")
        if (
            cli.soperator_reconcile_stage_plan_sha256(
                strategy=spec.strategy, rendered_graph_sha256=cli.soperator_stage_plan_sha256(local)
            )
            != spec.stage_plan_sha256
        ):
            raise RuntimeError("Upgrade stage graph changed")
        for filename, expected in (
            ("configmap-terraform-fluxcd-values.yaml", spec.desired_values_sha256),
            ("soperator-nebius-adapter.yaml", spec.adapter_sha256),
        ):
            from .deployment_state import _read_regular

            if (
                "sha256:" + hashlib.sha256(_read_regular(local.flux_dir / filename)).hexdigest()
                != expected
            ):
                raise RuntimeError("Upgrade frozen values or adapter changed")
        live = cli._live_soperator_release_for_reconcile(env=env)
        if live != spec.current_release:
            raise RuntimeError("Upgrade source release is no longer active")
        load_active_soperator_release_intent(
            paths=local,
            target_ref=target_ref,
            requested_selector=intent.requested_selector,
            ownership=intent.ownership,
            live_release=live,
            nebius_cluster_id=spec.nebius_cluster_id,
            kubernetes_uid=spec.kubernetes_uid,
        )
        require_pre_promotion(receipt, journal.snapshot())
        authority = rootfs_storage_authority(adapter, preflight.target_slot)
        if authority != preflight.storage_authority:
            raise RuntimeError("Upgrade passive storage backing changed")
        pvc = kube.get("pvc", preflight.target_pvc_name, "soperator")
        pv = kube.get("pv", authority["targetGeneration"]["pv_name"])
        pv_uid = verify_target_volume(authority, pvc=pvc, pv=pv)
        binding = journal.stage("rootfs-passive-target-identity")
        if (
            not binding
            or binding.get("status") != "complete"
            or binding["evidence"].get("pvcUid") != pvc["metadata"]["uid"]
            or binding["evidence"].get("pvUid") != pv_uid
        ):
            raise RuntimeError("Upgrade passive PVC/PV identity changed")
        directories = observe_protected_directories(
            cli,
            {
                "jailPersistentMounts": [
                    {"mountPath": row["mount_path"], "localPath": row["local_path"]}
                    for row in authority["persistentMounts"]
                ]
            },
            kube_context=kube.context,
            extra_env=env,
        )
        if sorted(directories, key=lambda row: row["mountPath"]) != sorted(
            preflight.directory_identities, key=lambda row: row["mountPath"]
        ):
            raise RuntimeError("Upgrade persistent directory identities changed")
        pods = json.loads(kube.run(["get", "pods", "-A", "-o", "json"]))
        attempt = validate_chain(stages.stage(stage))
        # Resume may see its own running successor. Exempt only an authenticated Pod UID,
        # never every Pod carrying this operation's label.
        if len(stages.stage(stage)["attempts"]) > 1:
            name = attempt["manifest"]["metadata"]["name"]
            candidates = [
                p
                for p in pods["items"]
                if any(
                    o.get("name") == name and o.get("kind") == "Job"
                    for o in p.get("metadata", {}).get("ownerReferences", [])
                )
            ]
            if candidates:
                job = kube.get("job", name, "soperator")
                uid, workload = cli._ensure_protected_data_plane_job(
                    attempt["manifest"],
                    kube_context=kube.context,
                    extra_env=env,
                    allow_create=False,
                    expected_job_uid=attempt.get("jobUid"),
                    expected_workload_sha256=attempt.get("workloadSha256"),
                )
                if len(candidates) != 1 or not uid or not workload:
                    raise RuntimeError("Upgrade successor writer identity is ambiguous")
                protected_job_pod_identity(job=job, pod=candidates[0])
                pods["items"] = [
                    p
                    for p in pods["items"]
                    if p["metadata"]["uid"] != candidates[0]["metadata"]["uid"]
                ]
        resources = json.loads(
            kube.run(
                [
                    "get",
                    "slurmclusters.slurm.nebius.ai,nodesets.slurm.nebius.ai",
                    "-A",
                    "-o",
                    "json",
                ]
            )
        )
        verify_passive_rootfs_consumers(
            pvc_name=preflight.target_pvc_name, pods=pods, soperator_resources=resources
        )
        return {"pvcUid": pvc["metadata"]["uid"], "pvUid": pv_uid}

    binding = physical_fence()
    request = expected_manifest(
        stages,
        stage=stage,
        operation_id=operation_id,
        config=generation.manifest["runtime_config"],
        target_ref=target_ref,
        image=spec.target_jail_image,
        pvc=preflight.target_pvc_name,
        pvc_uid=binding["pvcUid"],
        pv_uid=binding["pvUid"],
        filesystem_id=adapter["filesystemId"],
        generation=operation_id,
    )
    epoch = observed_authority.fencing_epoch
    if not dry_run:
        lease = stack.enter_context(
            SoperatorOperationLease(
                kube_context=kube.context,
                cluster_id=spec.nebius_cluster_id,
                operation_fingerprint=operation_id,
                extra_env=env,
            )
        )

        def operation_fence():
            fence()
            lease.assert_held()
            anchor.assert_held()

        anchor = SoperatorOperationAnchor(
            kube_context=kube.context,
            cluster_id=spec.nebius_cluster_id,
            operation_spec=spec,
            lease_authority=lease.authority,
            extra_env=env,
        )
        # Recheck persisted owner and frontier before rebinding existing authority.
        if (
            journal.snapshot() != original
            or kube.get("configmap", anchor_name, "kube-system")["data"] != anchor_data
        ):
            raise RuntimeError("Upgrade owner changed during recovery admission")
        physical_fence()
        from .deployment_recovery import capture_execution_cache, execution_checkpoint

        executing = [
            (key, value)
            for key, value in record.value["active"]["stages"].items()
            if value.get("status") == "executing"
        ]
        if len(executing) != 1:
            raise RuntimeError("Upgrade recovery requires one exact executing deployment stage")
        stage_key, stage_evidence = executing[0]

        def checkpoint_cache():
            nonlocal record
            record = state.checkpoint(
                record,
                stage=stage_key,
                evidence={**stage_evidence, "recovery": capture_execution_cache(paths.repo_root)},
            )

        stack.enter_context(execution_checkpoint(checkpoint_cache))
        anchor.establish()
        journal = make_journal(lease.authority, operation_fence)
        journal.establish()
        stages = RootfsStages(journal)
        epoch = lease.authority.fencing_epoch
        prior_fence = fence

        def fence():
            prior_fence()
            lease.assert_held()
            anchor.assert_held()

    recover_stage(
        cli,
        stages,
        stage=stage,
        predecessor_uid=job_uid,
        epoch=epoch,
        manifest=request,
        fence=physical_fence,
        env=env,
        dry_run=dry_run,
    )
    cli.console.print(command, markup=False, soft_wrap=True)
