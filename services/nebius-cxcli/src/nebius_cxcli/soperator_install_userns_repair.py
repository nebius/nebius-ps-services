"""Exact adapter-only recovery for a proven native Enroot namespace failure."""

from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from .paths import ProjectPaths
from .soperator_checks import SoperatorChecksExecution
from .soperator_checks_policy import SoperatorChecksPolicy, compile_checks_policy
from .soperator_enroot import ENROOT_PROFILE_NAME, enroot_profile_document
from .soperator_install_checks_repair import _publish_binding_repair, _verify_ancestor_seals
from .soperator_install_render_repair import (
    USERNS_REPAIR_REASON,
    VALUES_FILE,
    _bind_repair_admission,
    _digest,
    _documents,
    _file_hashes,
    _files,
    _kube_get,
)
from .soperator_install_userns_recovery import capture_userns_failure
from .soperator_receipt_io import read_owner_only_json
from .soperator_release import load_soperator_release_snapshot, soperator_release_snapshot_path
from .soperator_release_reconciler import validate_install_runtime_frontier
from .soperator_release_resolver import frozen_soperator_release_from_snapshot

ADAPTER_FILE = "soperator-nebius-adapter.yaml"


def userns_repair_candidate(
    previous: Mapping[str, bytes], *, inverse: bool = False
) -> dict[str, bytes]:
    raw = previous[ADAPTER_FILE]
    documents = _documents(raw)
    labels = {**documents[0]["metadata"]["labels"], "soperator.nebius.ai/lifecycle": "recreatable"}
    if labels.get("soperator.nebius.ai/managed-by") != "nebius-cxcli-adapter":
        raise RuntimeError("Enroot repair requires the owned adapter")
    expected = enroot_profile_document(labels)
    suffix = b"---\n" + yaml.safe_dump(expected, sort_keys=False).encode()
    profiles = [d for d in documents if d.get("kind") == "AppArmorProfile"]
    if inverse:
        if profiles != [expected] or not raw.endswith(suffix):
            raise RuntimeError("Enroot repair lost the exact additive profile delta")
        candidate = raw[: -len(suffix)]
    elif profiles:
        if profiles != [expected]:
            raise RuntimeError("Enroot repair cannot replace an existing profile")
        return dict(previous)
    else:
        if not raw.endswith(b"\n"):
            raise RuntimeError("Enroot repair requires canonical adapter document boundaries")
        candidate = raw + suffix
    return {**previous, ADAPTER_FILE: candidate}


def userns_reservation_handoff(
    repair: Mapping[str, Any], *, paths: ProjectPaths, policy: SoperatorChecksPolicy
) -> Mapping[str, Any]:
    current = _files(paths.flux_dir)
    previous = userns_repair_candidate(current, inverse=True)
    if (
        _file_hashes(current) != repair["replacementFiles"]
        or _file_hashes(previous) != repair["previousFiles"]
        or userns_repair_candidate(previous) != current
        or repair["reservationHandoff"]["policy"] != policy.sha256
    ):
        raise RuntimeError("Enroot recovery lost its exact adapter and native policy binding")
    handoff = repair["reservationHandoff"]
    return {**handoff, "predecessorPolicy": handoff["policy"]}


def prepare_install_userns_repair(
    *,
    paths: ProjectPaths,
    target_ref: str,
    scheduling_journal: Mapping[str, Any],
    local_scheduling_journal: Mapping[str, Any] | None,
    env: Mapping[str, str],
    kube_context: str,
    assert_authority: Callable[[], object],
    slurm: Callable[[str], str] | None,
    ancestor: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    repair_path = paths.reports_dir / f"soperator-install-userns-repair-{target_ref}.json"
    existing = _files(paths.flux_dir)
    if repair_path.exists():
        saved = read_owner_only_json(repair_path, label="Soperator Enroot repair")
        if (
            not isinstance(saved, Mapping)
            or saved.get("schema") != USERNS_REPAIR_REASON
            or saved.get("targetRef") != target_ref
            or saved.get("replacementFiles") != _file_hashes(existing)
        ):
            raise RuntimeError("saved Enroot repair lost generated authority")
        _verify_ancestor_seals(
            saved, env=env, kube_context=kube_context, assert_authority=assert_authority
        )
        _bind_repair_admission(
            saved,
            env=env,
            kube_context=kube_context,
            assert_authority=assert_authority,
            create=False,
        )
        return saved
    if ancestor is None or ancestor.get("previousOperationSpecSha256") == scheduling_journal.get(
        "operationSpecSha256"
    ):
        return ancestor
    candidate = userns_repair_candidate(existing)
    if candidate == existing:
        return ancestor
    if (
        scheduling_journal.get("lastCompletedStage") != "infrastructure-restored"
        or scheduling_journal.get("status") != "recovery-required"
        or scheduling_journal.get("actions") != []
        or local_scheduling_journal != scheduling_journal
        or slurm is None
    ):
        raise RuntimeError("Enroot repair requires exact initial scheduling recovery")
    previous_sha = scheduling_journal["operationSpecSha256"]
    predecessors = [
        row
        for path in paths.reports_dir.glob("soperator-release-reconcile-*.json")
        if isinstance(row := read_owner_only_json(path, label="Enroot predecessor"), Mapping)
        and _digest(row.get("operation", {}).get("spec")) == previous_sha
    ]
    if len(predecessors) != 1:
        raise RuntimeError("Enroot repair requires one exact predecessor")
    predecessor = predecessors[0]
    validate_install_runtime_frontier(predecessor)
    spec, hashes = predecessor["operation"]["spec"], _file_hashes(existing)
    snapshot = load_soperator_release_snapshot(
        soperator_release_snapshot_path(paths.reports_dir, target_ref)
    )
    if (
        spec.get("target_ref") != target_ref
        or spec.get("target_release") != snapshot.release
        or spec.get("desired_values_sha256") != hashes[VALUES_FILE]
        or spec.get("adapter_sha256") != hashes[ADAPTER_FILE]
        or ancestor.get("replacementFiles") != hashes
        or ancestor.get("interventionGeneration") != spec.get("intervention_generation")
        or userns_repair_candidate(candidate, inverse=True) != existing
    ):
        raise RuntimeError("Enroot repair changed source, storage, policy or ancestry")
    values = yaml.safe_load(_documents(existing[VALUES_FILE])[0]["data"]["values.yaml"])
    frozen = frozen_soperator_release_from_snapshot(snapshot)
    policy = compile_checks_policy(Path(frozen.source.source_dir), values)

    def read_kube(args: list[str], document: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if args[0] != "get" or document is not None:
            raise RuntimeError("Enroot admission is read-only")
        result = subprocess.run(
            ["kubectl", "--context", kube_context, *args],
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        return json.loads(result.stdout or "{}")

    check_path = (
        paths.reports_dir / f"soperator-checks-{previous_sha.removeprefix('sha256:')[:24]}.json"
    )
    if not check_path.exists():
        raise RuntimeError("Enroot predecessor checks are missing")
    runner = SoperatorChecksExecution(
        policy=policy,
        operation_id=previous_sha,
        receipt_path=check_path,
        kubernetes=read_kube,
        slurm=slurm,
        assert_authority=assert_authority,
    )
    state = copy.deepcopy(runner.state)
    failure = capture_userns_failure(runner, env=env, kube_context=kube_context)
    profiles = _kube_get(["apparmorprofiles", "-A"], env=env, kube_context=kube_context)
    if profiles is None or any(
        p.get("metadata", {}).get("name") == ENROOT_PROFILE_NAME
        or "enroot-nsenter" in str(p.get("spec", {}).get("policy", ""))
        for p in profiles.get("items", [])
    ):
        raise RuntimeError("Enroot repair cannot adopt an existing namespace profile")
    child = _kube_get(
        ["helmrelease", "cxcli-soperator-fluxcd-security-profiles-operator", "-n", "flux-system"],
        env=env,
        kube_context=kube_context,
    )
    if (
        not child
        or not child.get("metadata", {}).get("uid")
        or child.get("metadata", {}).get("deletionTimestamp")
        or child.get("spec", {}).get("releaseName") != "security-profiles-operator"
        or child.get("spec", {}).get("targetNamespace") != "security-profiles-operator-system"
        or child.get("spec", {}).get("values", {}).get("enableAppArmor") is not True
        or child.get("status", {}).get("observedGeneration")
        != child.get("metadata", {}).get("generation")
        or not any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in child.get("status", {}).get("conditions", [])
        )
        or any(
            c.get("type") in {"Stalled", "Reconciling"} and c.get("status") == "True"
            for c in child.get("status", {}).get("conditions", [])
        )
    ):
        raise RuntimeError("Enroot repair lost its existing upstream profile owner")
    if (
        read_owner_only_json(check_path, label="Enroot predecessor checks") != state
        or capture_userns_failure(runner, env=env, kube_context=kube_context) != failure
    ):
        raise RuntimeError("Enroot failure evidence changed during admission")
    return _publish_binding_repair(
        paths=paths,
        target_ref=target_ref,
        scheduling_journal=scheduling_journal,
        existing=existing,
        candidate=candidate,
        predecessor=predecessor,
        ancestor=ancestor,
        reason=USERNS_REPAIR_REASON,
        repair_path=repair_path,
        child_key="profileRelease",
        child_evidence={"uid": child["metadata"]["uid"], "specSha256": _digest(child["spec"])},
        hook_evidence=None,
        env=env,
        kube_context=kube_context,
        assert_authority=assert_authority,
        additional_evidence={
            "usernsFailure": failure,
            "reservationHandoff": {
                "operation": previous_sha,
                "receiptSha256": _digest(state),
                "policy": policy.sha256,
                "reservation": failure["reservation"]["name"],
                "fingerprint": failure["reservation"]["fingerprint"],
            },
        },
    )
