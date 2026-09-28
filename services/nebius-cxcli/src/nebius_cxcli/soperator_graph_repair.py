"""A sealed successor for the exact same-release, failed graph-apply frontier."""

from __future__ import annotations

import copy
import shlex
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .soperator_failures import SoperatorSafetyPauseError
from .soperator_graph_transition import REPAIR_REASON, NativeGraphTransition
from .soperator_operation import soperator_sha256
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json


def validate_frontier(receipt: Mapping[str, Any]) -> None:
    from .soperator_release_reconciler import (
        SOPERATOR_RECONCILE_RECEIPT_SCHEMA,
        _validate_existing_transition_chain,
    )

    spec = receipt.get("operation", {}).get("spec", {})
    rows = receipt.get("transitions", [])
    if (
        receipt.get("schema") != SOPERATOR_RECONCILE_RECEIPT_SCHEMA
        or receipt.get("status") != "recovery-required"
        or spec.get("strategy") != "in-place"
        or not spec.get("current_release")
        or spec.get("current_release") != spec.get("target_release")
        or [(r.get("phase"), r.get("status")) for r in rows]
        != [
            ("resolve-immutable-sources", "complete"),
            ("establish-boot-storage-barrier", "complete"),
            ("apply-declarative-release", "failed"),
        ]
        or receipt.get("irreversibleFrontier") is not None
        or not receipt.get("operationId")
    ):
        raise SoperatorSafetyPauseError(
            "Native graph repair requires the exact same-release failed-apply frontier"
        )
    _validate_existing_transition_chain(rows, operation_id=receipt["operationId"])


def validate_successor(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    if (
        changed != {"admission_sha256", "intervention_generation"}
        or after["intervention_generation"] != before["intervention_generation"] + 1
    ):
        raise SoperatorSafetyPauseError(
            "Native graph successor changed immutable deployment inputs"
        )


def _read(path: Path) -> dict[str, Any]:
    result = read_owner_only_json(path, label="Native graph recovery")
    if not isinstance(result, dict):
        raise SoperatorSafetyPauseError("Native graph recovery receipt is invalid")
    return result


def _path(paths: Any, target: str) -> Path:
    token = soperator_sha256(target).split(":")[-1][:24]
    return paths.reports_dir / f"soperator-recovery-native-graph-{token}.json"


def verify_source_fences(repair: Mapping[str, Any], transition: NativeGraphTransition) -> None:
    from .soperator_graph_transition import canonical_spec, identity, owned_by, spec_digest

    if transition.state["phase"] == "verified-absent":
        transition.assert_cleanup()
    parent = transition.admission["parent"]
    published = transition.state.get("publishedSpecSha256")
    parent_live = transition._published_parent() if published else None
    manifest = None
    for row in repair["checks"]["-source"].get("sourceWriters", []):
        live = transition.get(row["kind"], row["namespace"], row["name"])
        is_parent = (row["namespace"], row["name"], row["uid"]) == (
            parent["namespace"],
            parent["name"],
            parent["uid"],
        )
        if live.get("metadata", {}).get("uid") != row["uid"]:
            raise SoperatorSafetyPauseError(
                "Source diagnostic writer escaped its frozen maintenance fence"
            )
        if is_parent and parent_live is not None:
            continue
        if spec_digest(live) == row["contract"] and live.get("spec", {}).get("suspend") is True:
            continue
        # Parent publication may already have rewritten retained child writers.
        # Prove that postimage from the exact current Helm revision rather than
        # treating completed writer cleanup as authority for every source writer.
        if row["kind"] == "helmrelease" and parent_live is not None and owned_by(live, parent_live):
            status = parent_live.get("status", {})
            history = status.get("history", [])
            proof = transition.state.get("parentReconciliation", {})
            reconciled_now = (
                status.get("observedGeneration") == parent_live["metadata"].get("generation")
                and history
                and history[0].get("status") == "deployed"
                and any(
                    c.get("type") == "Ready" and c.get("status") == "True"
                    for c in status.get("conditions", [])
                )
            )
            reconciled_before_fence = (
                history
                and history[0].get("status") == "deployed"
                and str(history[0]["version"]) == proof.get("revision")
                and proof.get("specSha256")
                in {published, *transition.state.get("parentPublication", {}).values()}
            )
            if reconciled_now or reconciled_before_fence:
                if manifest is None:
                    manifest = transition.parent_manifest(parent_live)
                    if not reconciled_now and soperator_sha256(manifest) != proof.get(
                        "manifestSha256"
                    ):
                        raise SoperatorSafetyPauseError(
                            "Native parent Helm revision changed after fencing"
                        )
                matches = [
                    document
                    for document in manifest
                    if document.get("kind") == "HelmRelease"
                    and identity(document) == identity(live)
                ]
                if len(matches) == 1:
                    expected_spec = canonical_spec(transition.run, live, matches[0]["spec"])
                    from .soperator_child_publication import pending_empty_values

                    if spec_digest({"spec": expected_spec}) != spec_digest(
                        live
                    ) and pending_empty_values(
                        transition, parent_live, live, manifest, expected_spec
                    ):
                        # Still suspended and pending an exact product-owned CAS.
                        # This permits recovery, never child execution/readiness.
                        continue
                    opened = transition.state.get("openedChildren", {}).get(
                        row["namespace"] + "/" + row["name"], {}
                    )
                    stage_opened = opened.get("uid") == row["uid"] and opened.get(
                        "specSha256"
                    ) == spec_digest(live)
                    if spec_digest({"spec": expected_spec}) == spec_digest(live) and (
                        expected_spec.get("suspend") is not True
                        or live["spec"].get("suspend") is True
                        or stage_opened
                    ):
                        continue
        raise SoperatorSafetyPauseError(
            "Source diagnostic writer escaped its frozen maintenance fence"
        )


def prepare_repair(
    paths: Any,
    transition: NativeGraphTransition,
    *,
    bound_sha256: str,
    scheduling: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    if not bound_sha256:
        return None
    target = transition.admission["target"]
    path = _path(paths, target["targetRef"])
    if path.exists():
        intent = _read(path)
        if (
            intent.get("schema") != REPAIR_REASON
            or bound_sha256
            not in {
                intent["previousOperationSpecSha256"],
                soperator_sha256(intent["successorSpec"]),
            }
            or intent["transitionSha256"] != transition.state["admissionSha256"]
            or intent["schedulingSha256"] != soperator_sha256(scheduling)
        ):
            raise SoperatorSafetyPauseError(
                "Native graph successor conflicts with the active checkpoint"
            )
        verify_source_fences(intent["repair"], transition)
        return intent["repair"]
    if transition.state.get("boundOperationSpecSha256") == bound_sha256:
        return None
    matches = []
    for file in paths.reports_dir.glob("soperator-release-reconcile-*.json"):
        value = _read(file)
        if soperator_sha256(value.get("operation", {}).get("spec", {})) == bound_sha256:
            matches.append(value)
    if len(matches) != 1:
        raise SoperatorSafetyPauseError(
            "Native graph repair requires one exact predecessor receipt"
        )
    predecessor = matches[0]
    spec = predecessor["operation"]["spec"]
    # An already-bound transition is an ordinary replay, not a new successor.
    if spec.get("admission_sha256") == soperator_sha256(
        {"nativeGraphTransition": transition.admission}
    ):
        return None
    validate_frontier(predecessor)
    if any(
        spec.get(k) != target[v]
        for k, v in (
            ("target_ref", "targetRef"),
            ("nebius_cluster_id", "clusterId"),
            ("kubernetes_uid", "kubernetesUid"),
        )
    ):
        raise SoperatorSafetyPauseError("Native graph predecessor belongs to another target")
    receipts = {}
    for suffix in ("", "-source"):
        receipt = _read(
            paths.reports_dir / f"soperator-checks-{bound_sha256.split(':')[-1][:24]}{suffix}.json"
        )
        if (
            receipt.get("schema") != "nebius-cxcli.soperator-checks-execution.v2"
            or receipt.get("operation") != bound_sha256 + (":source" if suffix else "")
            or receipt.get("targetApplyIntent") is not True
            or receipt.get("jobs") != {}
            or any(receipt.get(key) for key in ("acceptance", "scheduleRelease"))
            or receipt.get("validation", {}).get("readiness") != "pending"
            or receipt.get("validation", {}).get("extended") != "pending"
            or receipt.get("validation", {}).get("profile") is not None
            or (not suffix and receipt.get("policy") != spec.get("checks_policy_sha256"))
        ):
            raise SoperatorSafetyPauseError(
                "Native graph repair lost its exact maintenance preimages"
            )
        receipts[suffix or "target"] = receipt
    parent = transition.admission["parent"]
    source_writers = receipts["-source"].get("sourceWriters", [])
    if not any(
        row.get("kind") == "helmrelease"
        and (row.get("namespace"), row.get("name"), row.get("uid"), row.get("contract"))
        == (parent["namespace"], parent["name"], parent["uid"], parent["specSha256"])
        for row in source_writers
    ):
        raise SoperatorSafetyPauseError(
            "Live umbrella does not match the frozen source-writer preimage"
        )
    repair = {
        "schema": REPAIR_REASON,
        "previousOperationSpecSha256": bound_sha256,
        "interventionGeneration": spec["intervention_generation"] + 1,
        "predecessorReceipt": predecessor,
        "checks": receipts,
        "transitionSha256": transition.state["admissionSha256"],
        "schedulingSha256": soperator_sha256(scheduling),
    }
    verify_source_fences(repair, transition)
    return repair


def seal_successor(
    paths: Any, spec: Any, repair: Mapping[str, Any], authority: Callable[[], object]
) -> None:
    previous = repair["predecessorReceipt"]["operation"]["spec"]
    validate_successor(previous, asdict(spec))
    intent = {
        "schema": REPAIR_REASON,
        "successorSpec": asdict(spec),
        "repair": dict(repair),
        "previousOperationSpecSha256": repair["previousOperationSpecSha256"],
        "transitionSha256": repair["transitionSha256"],
        "schedulingSha256": repair["schedulingSha256"],
    }
    path = _path(paths, spec.target_ref)
    if path.exists():
        if _read(path) != intent:
            raise SoperatorSafetyPauseError("Native graph successor seal changed")
        return
    authority()
    write_owner_only_json(path, intent)


def transfer_checks(checks: Any, repair: Mapping[str, Any], *, source: bool = False) -> None:
    previous = repair["checks"]["-source" if source else "target"]
    if previous["policy"] != checks.state["policy"]:
        raise SoperatorSafetyPauseError("Native graph repair changed the diagnostic policy")
    if previous["validation"] != checks.state["validation"] and not checks.state.get(
        "nativeGraphPredecessor"
    ):
        raise SoperatorSafetyPauseError("Native graph repair changed acceptance controls")
    expected = copy.deepcopy(previous)
    expected["operation"] = checks.state["operation"]
    expected["nativeGraphPredecessor"] = soperator_sha256(previous)
    if checks.state.get("nativeGraphPredecessor"):
        if checks.state["nativeGraphPredecessor"] != expected["nativeGraphPredecessor"]:
            raise SoperatorSafetyPauseError(
                "Native graph checks handoff belongs to another predecessor"
            )
        return
    if checks.state.get("jobs") or checks.state.get("targetApplyIntent"):
        raise SoperatorSafetyPauseError(
            "Native graph checks successor already has independent state"
        )
    checks.authority()
    checks.state = expected
    checks._save()


def sealed_binding(paths: Any, target: str, successor_sha256: str) -> tuple[str, int] | None:
    path = _path(paths, target)
    if not path.exists():
        return None
    intent = _read(path)
    if (
        intent.get("schema") != REPAIR_REASON
        or soperator_sha256(intent.get("successorSpec", {})) != successor_sha256
    ):
        return None
    repair = intent["repair"]
    if repair["previousOperationSpecSha256"] != intent["previousOperationSpecSha256"]:
        raise SoperatorSafetyPauseError("Native graph binding seal is inconsistent")
    validate_frontier(repair["predecessorReceipt"])
    validate_successor(repair["predecessorReceipt"]["operation"]["spec"], intent["successorSpec"])
    return intent["previousOperationSpecSha256"], intent["successorSpec"]["intervention_generation"]


def verify_scheduling_maintenance(cli: Any, journal: Mapping[str, Any], extra_env: Any) -> None:
    drained, partitions, requeued, held, reservations = (
        cli._soperator_flux_apply_owned_scheduling_state(journal["actions"])
    )
    for namespace, rows in partitions.items():
        for row in rows:
            observed = cli._soperator_upgrade_partition_state(
                namespace=namespace,
                partition=row.partition,
                extra_env=extra_env,
            )
            if not cli._soperator_upgrade_partition_observation_matches(
                observed,
                record=row.applied_record,
                fingerprint=row.applied_record_fingerprint,
            ):
                raise SoperatorSafetyPauseError("Native repair partition maintenance changed")
    for namespace in set(requeued) | set(held):
        expected = requeued.get(namespace, set()) | held.get(namespace, set())
        events = [event for event in journal["actions"] if event.get("namespace") == namespace]
        postimages = {}
        for event in events:
            if event.get("action") in {
                "requeue-hold-applied",
                "requeue-hold-selected-applied",
                "requeue-hold-all-applied",
                "pending-hold-applied",
            }:
                for row in event.get("jobs", []):
                    job_id = row.get("job_id")
                    if not job_id or job_id in postimages:
                        raise SoperatorSafetyPauseError(
                            "Native repair held-job postimage is ambiguous"
                        )
                    postimages[job_id] = row
        jobs = cli._soperator_upgrade_jobs_by_id(
            namespace=namespace,
            job_ids=sorted(expected),
            extra_env=extra_env,
        )
        if {job.job_id for job in jobs} != expected or any(
            not cli.slurm_job_is_held(job) for job in jobs
        ):
            raise SoperatorSafetyPauseError("Native repair held-job ownership changed")
        for job in jobs:
            if job.job_id not in postimages or any(
                str(postimages[job.job_id].get(field) or "").strip()
                != str(getattr(job, field)).strip()
                for field in ("user", "state", "partition", "allocated_nodes", "reason", "name")
            ):
                raise SoperatorSafetyPauseError("Native repair held-job postimage changed")
        records = {row.job_id: row for row in cli.applied_slurm_held_job_records(events)}
        for job_id in requeued.get(namespace, set()):
            if job_id not in records:
                raise SoperatorSafetyPauseError(
                    "Native repair held job has no frozen control identity"
                )
            result = cli._run_soperator_upgrade_login_command(
                namespace,
                "scontrol show job " + shlex.quote(job_id) + " -o",
                extra_env=extra_env,
                timeout_seconds=120,
                check=False,
            )
            live = cli.slurm_job_control_record_from_query(
                requested_job_id=job_id,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
            if live is None or live != records[job_id]:
                raise SoperatorSafetyPauseError("Native repair held-job control identity changed")
    for namespace, names in reservations.items():
        if not names <= set(
            cli._soperator_upgrade_reservation_names(namespace=namespace, extra_env=extra_env)
        ):
            raise SoperatorSafetyPauseError("Native repair maintenance reservation disappeared")
    for namespace, names in drained.items():
        expected_nodes = {}
        for event in journal["actions"]:
            if event.get("namespace") == namespace:
                expected_nodes.update(event.get("node_postimage", {}))
        live_nodes = cli._soperator_upgrade_node_recovery_snapshot(
            namespace=namespace,
            node_names=sorted(names),
            extra_env=extra_env,
        )
        if any(
            name not in expected_nodes or live_nodes.get(name) != expected_nodes[name]
            for name in names
        ):
            raise SoperatorSafetyPauseError("Native repair node maintenance changed")
