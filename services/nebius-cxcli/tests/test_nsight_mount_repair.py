import copy
import json
from types import SimpleNamespace

import pytest

from nebius_cxcli import nsight_mount_repair as repair
from nebius_cxcli import nsight_recovery as recovery
from nebius_cxcli.deployment_state import digest
from test_nsight_recovery import Journal, failed_attempt, manifest


def old_chain():
    chain, _, _, _ = failed_attempt()
    for value in (chain["baseManifest"], chain["attempts"][0]["manifest"]):
        value["spec"]["template"]["spec"]["containers"][0]["securityContext"].pop("appArmorProfile")
    chain["attempts"][0]["workloadSha256"] = digest("old-workload")
    body = {
        "jobUid": "failed-uid",
        "workloadSha256": digest("old-workload"),
        "podUid": "pod-uid",
        "podIdentitySha256": digest("pod"),
        "terminated": {"rootfs": {"exitCode": 1}, "gate": {"exitCode": 0}},
        "mountFailure": {
            "signatureSha256": digest(repair.SIGNATURE),
            "boundary": "initial-private-mount",
        },
    }
    return chain, {**body, "proofSha256": digest(body)}


def test_renderer_explicitly_allows_mount_setup_without_privileged_container():
    context = manifest()["spec"]["template"]["spec"]["containers"][0]["securityContext"]
    assert context["appArmorProfile"] == {"type": "Unconfined"}
    assert not context.get("privileged") and context["allowPrivilegeEscalation"] is False


def test_repair_preserves_original_history_and_reserves_exactly_one_successor():
    chain, proof = old_chain()
    before = copy.deepcopy(chain)
    successor = recovery.reserve_successor(
        chain, predecessor_uid="failed-uid", epoch=2, proof=proof, repair=repair.REPAIR
    )
    recovery.validate_transition(before, successor)
    assert successor["baseManifest"] == before["baseManifest"]
    assert successor["attempts"][0] == before["attempts"][0]
    assert successor["attempts"][1]["repair"] == repair.repair_record(before["baseManifest"])
    assert (
        recovery.reserve_successor(
            successor, predecessor_uid="failed-uid", epoch=3, proof=proof, repair=repair.REPAIR
        )
        == successor
    )
    assert recovery.execution_manifest(successor) == repair.corrected_manifest(
        before["baseManifest"]
    )


@pytest.mark.parametrize(
    "mutation", ["script", "image", "volume", "capabilities", "repair", "proof"]
)
def test_repaired_history_rejects_every_unrelated_delta(mutation):
    chain, proof = old_chain()
    successor = recovery.reserve_successor(
        chain, predecessor_uid="failed-uid", epoch=2, proof=proof, repair=repair.REPAIR
    )
    row = successor["attempts"][-1]
    container = row["manifest"]["spec"]["template"]["spec"]["containers"][0]
    if mutation == "script":
        container["command"][2] += "echo changed"
    if mutation == "image":
        container["image"] += "changed"
    if mutation == "volume":
        container["volumeMounts"][0]["readOnly"] = True
    if mutation == "capabilities":
        container["securityContext"]["capabilities"]["add"].append("SYS_PTRACE")
    if mutation == "repair":
        row["repair"]["afterSha256"] = digest("foreign")
    if mutation == "proof":
        row["predecessor"]["mountFailure"]["boundary"] = "later"
    with pytest.raises(RuntimeError):
        recovery.validate_chain(successor)


@pytest.mark.parametrize(
    "logs", ["", repair.SIGNATURE + "package output\n", "mount: different failure\n"]
)
def test_missing_or_ambiguous_initial_failure_is_not_repair_authority(logs):
    chain, proof = old_chain()
    pod = {"metadata": {"name": "pod", "uid": "pod-uid"}}
    kube = SimpleNamespace(
        run=lambda args: logs if "logs" in args else json.dumps({"items": [pod]})
    )
    with pytest.raises(RuntimeError, match="exact initial"):
        repair.add_failure_proof(kube, chain["attempts"][0], proof)


def test_only_exact_initial_failure_proves_repair_boundary():
    chain, proof = old_chain()
    proof.pop("mountFailure")
    pod = {"metadata": {"name": "pod", "uid": "pod-uid"}}
    kube = SimpleNamespace(
        run=lambda args: repair.SIGNATURE if "logs" in args else json.dumps({"items": [pod]})
    )
    assert repair.has_failure_proof(repair.add_failure_proof(kube, chain["attempts"][0], proof))


def test_normal_resume_cannot_grant_a_repair(monkeypatch):
    chain, _ = old_chain()
    with pytest.raises(RuntimeError, match="Frozen Nsight"):
        recovery.run_attempt(
            SimpleNamespace(),
            Journal(chain),
            stage="nsight-admit",
            manifest=repair.corrected_manifest(chain["baseManifest"]),
            fence=lambda: None,
            context="",
            env={},
        )


def test_runtime_image_repair_composes_after_mount_repair_within_existing_budget():
    chain, proof = old_chain()
    for row in (chain["baseManifest"], chain["attempts"][0]["manifest"]):
        row["spec"]["template"]["spec"]["containers"][0]["image"] = repair.INCOMPATIBLE_IMAGE
    first = recovery.reserve_successor(
        chain, predecessor_uid="failed-uid", epoch=2, proof=proof, repair=repair.REPAIR
    )
    first["attempts"][-1].update(jobUid="second-uid", workloadSha256=digest("second-workload"))
    body = {k: v for k, v in proof.items() if k != "proofSha256"}
    body.update(jobUid="second-uid", workloadSha256=digest("second-workload"))
    body["mountFailure"] = {
        "signatureSha256": digest(repair.IMAGE_SIGNATURE),
        "boundary": "initial-path-check",
    }
    second_proof = {**body, "proofSha256": digest(body)}
    result = recovery.reserve_successor(
        first, predecessor_uid="second-uid", epoch=3, proof=second_proof, repair=repair.IMAGE_REPAIR
    )
    recovery.validate_transition(first, result)
    assert result["attempts"][:2] == first["attempts"]
    assert result["baseManifest"] == chain["baseManifest"]
    before = recovery.execution_manifest(first)
    after = recovery.execution_manifest(result)
    expected = copy.deepcopy(before)
    expected["spec"]["template"]["spec"]["containers"][0]["image"] = repair.RUNTIME_IMAGE
    assert after == expected
    assert (
        recovery.reserve_successor(
            result,
            predecessor_uid="second-uid",
            epoch=4,
            proof=second_proof,
            repair=repair.IMAGE_REPAIR,
        )
        == result
    )
    result["attempts"][-1].update(jobUid="third-uid", workloadSha256=digest("third-workload"))
    with pytest.raises(RuntimeError, match="three-Job"):
        recovery.reserve_successor(
            result, predecessor_uid="third-uid", epoch=4, proof={}, repair=""
        )


def test_image_repair_also_corrects_original_mount_policy_but_rejects_other_images():
    chain, proof = old_chain()
    with pytest.raises(RuntimeError, match="exact incompatible"):
        repair.corrected_manifest(chain["baseManifest"], repair.IMAGE_REPAIR)
    for row in (chain["baseManifest"], chain["attempts"][0]["manifest"]):
        row["spec"]["template"]["spec"]["containers"][0]["image"] = repair.INCOMPATIBLE_IMAGE
    result = recovery.reserve_successor(
        chain, predecessor_uid="failed-uid", epoch=2, proof=proof, repair=repair.IMAGE_REPAIR
    )
    assert (
        result["attempts"][-1]["manifest"]["spec"]["template"]["spec"]["containers"][0]["image"]
        == repair.RUNTIME_IMAGE
    )


def test_profile_order_changes_only_activation_and_authenticates_exact_failure():
    from nebius_cxcli.nsight_profiling import activation_command, customization_job
    from nebius_cxcli.soperator_protected_data_plane import bind_protected_job_authority

    current = bind_protected_job_authority(
        customization_job(
            name="cxcli-nsight-install",
            image="unused",
            pvc="jail",
            filesystem_id="fs",
            request={"action": "install", "admission": {}},
        ),
        operation_id=digest("operation"),
        fence_epoch=1,
        pvc_uid="pvc-uid",
    )
    before = copy.deepcopy(current)
    container = before["spec"]["template"]["spec"]["containers"][0]
    container["command"][2] = container["command"][2].replace(activation_command("install"), "", 1)
    assert repair.corrected_manifest(before, repair.PROFILE_REPAIR) == current
    with pytest.raises(RuntimeError, match="original install"):
        repair.corrected_manifest(current, repair.PROFILE_REPAIR)
    _, proof = old_chain()
    proof.pop("mountFailure")
    attempt = {"manifest": before, "jobUid": "failed-uid"}
    pod = {"metadata": {"uid": "pod-uid", "name": "pod"}}
    kube = SimpleNamespace(
        run=lambda args: (
            repair.PROFILE_SIGNATURE if "logs" in args else json.dumps({"items": [pod]})
        )
    )
    authenticated = repair.add_failure_proof(kube, attempt, proof, repair.PROFILE_REPAIR)
    assert repair.has_failure_proof(authenticated, before, repair.PROFILE_REPAIR)
    kube.run = lambda args: (
        repair.PROFILE_SIGNATURE + "different failure\n"
        if "logs" in args
        else json.dumps({"items": [pod]})
    )
    with pytest.raises(RuntimeError, match="exact initial"):
        repair.add_failure_proof(kube, attempt, proof, repair.PROFILE_REPAIR)
