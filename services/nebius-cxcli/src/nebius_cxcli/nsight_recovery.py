"""Nsight-only, identity-bound Job attempts shared by two journal owners."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping

from .deployment_state import digest
from .nsight_mount_repair import (
    add_failure_proof,
    corrected_manifest,
    has_failure_proof,
    repair_record,
    repair_stage,
)
from .nsight_profiling import parse_result
from .soperator_protected_data_plane import (
    SOPERATOR_PROTECTED_ADMITTED_WORKLOAD_ANNOTATION,
    SOPERATOR_PROTECTED_REQUESTED_WORKLOAD_ANNOTATION,
    protected_job_pod_identity,
    protected_workload_identity,
)

SCHEMA = "nebius-cxcli.nsight-attempts.v1"
STAGES = ("nsight-admit", "nsight-install", "nsight-verify")
MAX_JOBS = 3


def attempt_manifest(base, index, epoch):
    result = copy.deepcopy(base)
    name = base["metadata"]["name"]
    result["metadata"]["name"] = name if index == 0 else f"{name}-r{index}"
    if len(result["metadata"]["name"]) > 63:
        raise RuntimeError("Nsight successor Job name is too long")
    for metadata in (result["metadata"], result["spec"]["template"]["metadata"]):
        metadata["labels"]["nebius-cxcli/fence-epoch"] = str(epoch)
    return result


def initial_chain(manifest):
    epoch = int(manifest["metadata"]["labels"]["nebius-cxcli/fence-epoch"])
    return {
        "schema": SCHEMA,
        "baseManifest": copy.deepcopy(manifest),
        "attempts": [{"manifest": copy.deepcopy(manifest), "epoch": epoch, "complete": False}],
    }


def validate_chain(chain, *, completed=False):
    if not isinstance(chain, Mapping) or chain.get("schema") != SCHEMA:
        raise RuntimeError("Nsight recovery requires its original attempt journal")
    rows = chain.get("attempts")
    base = chain.get("baseManifest")
    if (
        not isinstance(base, Mapping)
        or not isinstance(rows, list)
        or not 1 <= len(rows) <= MAX_JOBS
    ):
        raise RuntimeError("Nsight attempt history is incomplete or exhausted")
    seen = set()
    execution = base
    repaired = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or type(row.get("epoch")) is not int or row["epoch"] < 1:
            raise RuntimeError("Nsight attempt authority is incomplete")
        if row.get("repair") is not None:
            kind = row["repair"].get("kind")
            if (
                index == 0
                or kind in repaired
                or protected_workload_identity(base).purpose
                != repair_stage(kind).replace("nsight-", "profiling-")
                or row["repair"] != repair_record(execution, kind)
                or not has_failure_proof(row.get("predecessor", {}), execution, kind)
            ):
                raise RuntimeError("Nsight runtime-mounts repair lineage is invalid")
            execution = corrected_manifest(execution, kind)
            repaired.add(kind)
        if row.get("manifest") != attempt_manifest(execution, index, row["epoch"]):
            raise RuntimeError("Nsight attempt changed its frozen execution")
        identity = protected_workload_identity(row["manifest"])
        if identity.purpose not in {"profiling-admit", "profiling-install", "profiling-verify"}:
            raise RuntimeError("Nsight recovery cannot own another rootfs stage")
        if row.get("jobUid"):
            if row["jobUid"] in seen or not row.get("workloadSha256"):
                raise RuntimeError("Nsight attempt reuses or lacks a workload identity")
            seen.add(row["jobUid"])
        elif row.get("complete"):
            raise RuntimeError("Completed Nsight attempt lacks its Job UID")
        if index:
            previous = rows[index - 1]
            proof = row.get("predecessor", {})
            body = {k: v for k, v in proof.items() if k != "proofSha256"}
            if (
                previous.get("complete")
                or not previous.get("jobUid")
                or proof.get("jobUid") != previous["jobUid"]
                or proof.get("workloadSha256") != previous.get("workloadSha256")
                or proof.get("proofSha256") != digest(body)
                or not proof.get("podUid")
                or not proof.get("podIdentitySha256")
                or not proof.get("terminated")
            ):
                raise RuntimeError("Nsight successor lacks authenticated predecessor termination")
        if row.get("complete") and not isinstance(row.get("result"), Mapping):
            raise RuntimeError("Nsight completed attempt lacks its result")
    if completed and not rows[-1].get("complete"):
        raise RuntimeError("Nsight attempt chain is incomplete")
    return rows[-1]


def execution_manifest(chain):
    result = chain["baseManifest"]
    for row in chain["attempts"]:
        if row.get("repair"):
            result = corrected_manifest(result, row["repair"]["kind"])
    return result


def reserve_successor(chain, *, predecessor_uid, epoch, proof, repair=""):
    validate_chain(chain)
    result = copy.deepcopy(chain)
    rows = result["attempts"]
    matches = [i for i, row in enumerate(rows) if row.get("jobUid") == predecessor_uid]
    if len(matches) != 1:
        raise RuntimeError("Recovery requires the exact recorded predecessor Job UID")
    index = matches[0]
    if index + 1 < len(rows):
        if repair and rows[index + 1].get("repair", {}).get("kind") != repair:
            raise RuntimeError("The reserved successor has a different repair intent")
        return result  # A lost response resumes this reservation; never allocate again.
    if rows[index].get("complete"):
        raise RuntimeError("A successful Nsight stage cannot be replaced")
    if len(rows) >= MAX_JOBS:
        raise RuntimeError("Nsight recovery exhausted its three-Job stage budget")
    base = execution_manifest(chain)
    correction = None
    if repair:
        correction = repair_record(base, repair)
        base = corrected_manifest(base, repair)
    rows.append(
        {
            "manifest": attempt_manifest(base, len(rows), epoch),
            "epoch": epoch,
            "complete": False,
            "predecessor": copy.deepcopy(proof),
        }
    )
    if repair:
        rows[-1]["repair"] = correction
    validate_chain(result)
    return result


def validate_transition(previous, chain):
    validate_chain(chain)
    if previous is None:
        if len(chain["attempts"]) != 1 or chain["attempts"][0].get("jobUid"):
            raise RuntimeError("Nsight history requires a write-ahead first attempt")
        return
    validate_chain(previous)
    old, new = previous["attempts"], chain["attempts"]
    if previous["baseManifest"] != chain["baseManifest"]:
        raise RuntimeError("Nsight original stage intent is immutable")
    if len(new) == len(old) + 1:
        if old != new[:-1] or new[-1]["epoch"] < old[-1]["epoch"]:
            raise RuntimeError("Nsight successor rewrote predecessor history")
    elif len(new) == len(old):
        if (
            old[:-1] != new[:-1]
            or any(
                old[-1].get(key) != new[-1].get(key)
                for key in ("manifest", "epoch", "predecessor", "repair")
            )
            or any(
                old[-1].get(key) and old[-1][key] != new[-1].get(key)
                for key in ("jobUid", "workloadSha256")
            )
            or (old[-1].get("complete") and old[-1] != new[-1])
        ):
            raise RuntimeError("Nsight recorded attempt identity is immutable")
    else:
        raise RuntimeError("Nsight recovery must reserve exactly one successor")


def terminal_proof(kube, attempt):
    """Read exact Job and Pod execution/termination, never infer quiescence from absence."""
    manifest = attempt["manifest"]
    metadata = manifest["metadata"]
    job = kube.get("job", metadata["name"], metadata["namespace"])
    meta, status = job.get("metadata", {}), job.get("status", {})
    annotations = meta.get("annotations", {})
    if (
        not attempt.get("jobUid")
        or meta.get("uid") != attempt["jobUid"]
        or meta.get("deletionTimestamp")
        or protected_workload_identity(job).workload_sha256 != attempt.get("workloadSha256")
        or annotations.get(SOPERATOR_PROTECTED_REQUESTED_WORKLOAD_ANNOTATION)
        != protected_workload_identity(manifest).workload_sha256
        or annotations.get(SOPERATOR_PROTECTED_ADMITTED_WORKLOAD_ANNOTATION)
        != attempt["workloadSha256"]
        or status.get("active", 0) != 0
        or not any(
            c.get("type") == "Failed" and c.get("status") == "True"
            for c in status.get("conditions", [])
        )
    ):
        raise RuntimeError("Nsight recovery requires its exact terminal failed Job")
    payload = json.loads(
        kube.run(
            [
                "-n",
                metadata["namespace"],
                "get",
                "pods",
                "-l",
                f"batch.kubernetes.io/controller-uid={attempt['jobUid']}",
                "-o",
                "json",
            ]
        )
    )
    pods = payload.get("items", [])
    if len(pods) != 1:
        raise RuntimeError("Nsight recovery requires one retained terminated Pod")
    pod = pods[0]
    pod_identity = protected_job_pod_identity(job=job, pod=pod)
    pmeta, pstatus = pod.get("metadata", {}), pod.get("status", {})
    if (
        not pmeta.get("uid")
        or pmeta.get("deletionTimestamp")
        or pstatus.get("phase") not in {"Failed", "Succeeded"}
    ):
        raise RuntimeError("Nsight predecessor Pod termination is not provable")
    terminated = {}
    for spec_key, status_key in (
        ("containers", "containerStatuses"),
        ("initContainers", "initContainerStatuses"),
    ):
        expected = {item["name"] for item in pod["spec"].get(spec_key, [])}
        statuses = pstatus.get(status_key, [])
        if {item.get("name") for item in statuses} != expected or len(statuses) != len(expected):
            raise RuntimeError("Nsight predecessor container termination evidence is missing")
        for item in statuses:
            final = item.get("state", {}).get("terminated")
            if not final or not final.get("finishedAt") or not item.get("containerID"):
                raise RuntimeError("Nsight predecessor still has an unaccounted writer")
            terminated[item["name"]] = {
                "containerId": item["containerID"],
                "finishedAt": final["finishedAt"],
                "exitCode": final["exitCode"],
            }
    body = {
        "jobUid": meta["uid"],
        "workloadSha256": attempt["workloadSha256"],
        "podUid": pmeta["uid"],
        "podIdentitySha256": pod_identity,
        "terminated": terminated,
    }
    return {**body, "proofSha256": digest(body)}


def run_attempt(cli, journal, *, stage, manifest, fence, context, env, retry_epoch=None):
    """Resume frozen execution; standalone installation may reserve a safe successor."""
    if stage not in STAGES:
        raise ValueError("Unsupported Nsight logical stage")
    fence()
    chain = journal.stage(stage)
    if chain is None:
        chain = initial_chain(manifest)
        journal.checkpoint(stage, chain)
    attempt = validate_chain(chain)
    first_epoch = chain["attempts"][0]["epoch"]
    if attempt_manifest(manifest, 0, first_epoch) != execution_manifest(chain):
        raise RuntimeError("Frozen Nsight installer or workload changed")
    if retry_epoch is not None:
        if attempt.get("complete"):
            return copy.deepcopy(attempt["result"])
        if attempt.get("jobUid"):
            from .nsight_runtime import NsightKubernetes

            kube = NsightKubernetes(env)
            metadata = attempt["manifest"]["metadata"]
            observed = kube.get("job", metadata["name"], metadata["namespace"])
            if any(
                row.get("type") == "Failed" and row.get("status") == "True"
                for row in observed.get("status", {}).get("conditions", [])
            ):
                if any(
                    journal.stage(later) is not None for later in STAGES[STAGES.index(stage) + 1 :]
                ):
                    raise RuntimeError("Nsight installation cannot rewind completed later stages")
                proof = terminal_proof(kube, attempt)
                successor = reserve_successor(
                    chain, predecessor_uid=attempt["jobUid"], epoch=retry_epoch, proof=proof
                )
                fence()
                journal.checkpoint(stage, successor)
                chain = successor
                attempt = validate_chain(chain)
                cli.console.print(
                    f"Nsight: resuming installation step {stage.removeprefix('nsight-')}."
                )
    job = attempt["manifest"]
    uid, workload = cli._ensure_protected_data_plane_job(
        job,
        kube_context=context,
        extra_env=env,
        allow_create=not attempt.get("jobUid"),
        expected_job_uid=attempt.get("jobUid"),
        expected_workload_sha256=attempt.get("workloadSha256"),
    )
    if not attempt.get("jobUid"):
        chain = copy.deepcopy(chain)
        chain["attempts"][-1].update(jobUid=uid, workloadSha256=workload)
        journal.checkpoint(stage, chain)
        attempt = validate_chain(chain)
    output = cli._wait_protected_data_plane_job(
        namespace=job["metadata"]["namespace"],
        name=job["metadata"]["name"],
        uid=uid,
        kube_context=context,
        extra_env=env,
        include_logs=True,
        expected_workload_sha256=workload,
    )
    fence()
    result = parse_result(output)
    if attempt.get("complete") and attempt["result"] != result:
        raise RuntimeError("Completed Nsight attempt result changed")
    chain = copy.deepcopy(chain)
    chain["attempts"][-1].update(complete=True, result=result)
    journal.checkpoint(stage, chain)
    return result


class RootfsStages:
    """Translate only Nsight logical stages; ConfigMap CAS remains the owner."""

    def __init__(self, journal):
        self.journal = journal

    def stage(self, name):
        row = self.journal.stage("rootfs-" + name)
        if row is None:
            return None
        if "nsight" not in row:
            raise RuntimeError("Nsight recovery requires the original attempt history")
        return row["nsight"]

    def checkpoint(self, name, chain):
        self.journal.checkpoint_nsight(name="rootfs-" + name, chain=chain)


def bind_attempt_receipt(receipt, journal):
    histories = {stage: journal.stage(stage) for stage in STAGES}
    for chain in histories.values():
        validate_chain(chain, completed=True)
    body = {key: value for key, value in receipt.items() if key != "receiptSha256"}
    body["attemptLineage"] = {stage: digest(chain) for stage, chain in histories.items()}
    return {**body, "receiptSha256": digest(body)}


def expected_manifest(journal, *, stage, operation_id, repair="", **generation_args):
    """Rebuild frozen execution from the current installer and authenticated owner inputs."""
    from .nsight_profiling import customize_generation
    from .soperator_protected_data_plane import bind_protected_job_authority

    class Selected(Exception):
        def __init__(self, manifest):
            self.manifest = manifest

    def collect(name, request):
        chain = journal.stage(name)
        validate_chain(chain, completed=name != stage)
        if name == stage:
            raise Selected(
                bind_protected_job_authority(
                    request,
                    operation_id=operation_id,
                    fence_epoch=chain["attempts"][0]["epoch"],
                    pvc_uid=generation_args["pvc_uid"],
                )
            )
        return chain["attempts"][-1]["result"]

    try:
        customize_generation(run_job=collect, **generation_args)
    except Selected as selected:
        chain = journal.stage(stage)
        expected = execution_manifest(chain)
        if repair and not any(
            row.get("repair", {}).get("kind") == repair for row in chain["attempts"]
        ):
            expected = corrected_manifest(expected, repair)
        if selected.manifest != expected:
            raise RuntimeError("Nsight recovery installer or frozen workload changed") from None
        return selected.manifest
    raise RuntimeError("The selected operation has no recoverable Nsight stage")


def recover_stage(
    cli, journal, *, stage, predecessor_uid, epoch, manifest, fence, env, dry_run, repair=""
):
    from .nsight_profiling import validate_customization
    from .nsight_runtime import NsightKubernetes

    fence()
    chain = journal.stage(stage)
    validate_chain(chain)
    matches = [i for i, row in enumerate(chain["attempts"]) if row.get("jobUid") == predecessor_uid]
    if len(matches) != 1:
        raise RuntimeError("Recovery requires the exact recorded failed Job UID")
    index = matches[0]
    kube = NsightKubernetes(env)
    proof = terminal_proof(kube, chain["attempts"][index])
    reserved_repair = index + 1 < len(chain["attempts"]) and chain["attempts"][index + 1].get(
        "repair"
    )
    if repair or reserved_repair:
        kind = repair or reserved_repair["kind"]
        if stage != repair_stage(kind):
            raise RuntimeError("Nsight runtime repair does not match its logical stage")
        proof = add_failure_proof(kube, chain["attempts"][index], proof, kind)
    if index + 1 < len(chain["attempts"]):
        if chain["attempts"][index + 1]["predecessor"] != proof:
            raise RuntimeError("Nsight predecessor termination evidence changed")
        if index + 2 != len(chain["attempts"]):
            raise RuntimeError("A later retry exists; select its exact failed Job UID")
        if chain["attempts"][-1].get("complete"):
            cli.console.print("The reserved Nsight successor already completed.")
            return
    if any(journal.stage(later) is not None for later in STAGES[STAGES.index(stage) + 1 :]):
        raise RuntimeError("Nsight recovery cannot rewind later stages")
    successor = reserve_successor(
        chain, predecessor_uid=predecessor_uid, epoch=epoch, proof=proof, repair=repair
    )
    name = successor["attempts"][-1]["manifest"]["metadata"]["name"]
    if dry_run:
        if repair:
            cli.console.print(
                "Exact repair: "
                + (
                    "add the owned late shell activation hook before the unchanged package installer"
                    if repair == "profile-order"
                    else "rootfs image: known incompatible population image -> pinned Ubuntu runtime; AppArmor Unconfined if absent"
                    if repair == "runtime-image"
                    else "rootfs.securityContext.appArmorProfile: absent -> {type: Unconfined}"
                )
                + "; all other frozen execution fields retained."
            )
        cli.console.print(
            f"Recovery admitted: {stage}, successor {name}, attempt {len(successor['attempts'])}/{MAX_JOBS}. No changes made."
        )
        return
    fence()
    journal.checkpoint(stage, successor)
    result = run_attempt(
        cli,
        journal,
        stage=stage,
        manifest=manifest,
        fence=fence,
        context=NsightKubernetes(env).context,
        env=env,
    )
    if stage == "nsight-admit":
        if result.get("schema") != "nebius-cxcli.nsight-packages.v2":
            raise RuntimeError("Nsight admission result is incomplete")
    else:
        admission = validate_chain(journal.stage("nsight-admit"), completed=True)["result"]
        validate_customization(result, admission)
        if (
            stage == "nsight-verify"
            and result != validate_chain(journal.stage("nsight-install"), completed=True)["result"]
        ):
            raise RuntimeError("Nsight verification differs from the installed result")
    cli.console.print(f"Recovered {stage}. Continue through the original operation below.")
