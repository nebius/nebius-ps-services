from __future__ import annotations

from types import SimpleNamespace

import pytest

from nebius_cxcli.compatibility_matrix import assess, digest, load_matrix
from nebius_cxcli.compatibility_transitions import assess_upgrade_states


def manifest(
    matrix,
    version="25.7.0",
    kubernetes="1.33",
    *,
    distribution="nvidia-upstream-network",
    constraint=None,
):
    subject = {
        "component_id": "nvidia-network-operator",
        "instance_id": "cluster",
        "kind": "helm",
        "owner": "cxcli",
        "enabled": True,
        "distribution": distribution,
        "application_version": version,
        "kubernetes_minor": kubernetes,
    }
    metadata = {"name": "network-operator", "version": version}
    if constraint:
        metadata["kubeVersion"] = constraint
    frozen = {
        "inventory": [subject],
        "report": assess([subject], matrix=matrix),
        "receipts": [
            {
                "subject_sha256": digest(subject),
                "evaluator": "exact_artifact_kube_version",
                "artifact": {"metadata": metadata},
            }
        ],
    }
    frozen["sha256"] = digest(frozen)
    return {"render": {"inputs": {"matrix": matrix}, "compatibility": frozen}}


def intent(source="1.33", target="1.34", hops=("1.34",)):
    return SimpleNamespace(
        target_ref="cluster",
        source_kubernetes_version=source,
        target_kubernetes_version=target,
        kubernetes_hops=hops,
        compatibility_rows=(),
        node_groups=(SimpleNamespace(key="worker", source_version=source, target_version=target),),
    )


def test_assesses_operand_rollout_control_plane_and_mixed_workers():
    matrix = load_matrix()
    report = assess_upgrade_states(manifest(matrix), manifest(matrix, "25.10.0", "1.34"), intent())
    assert [s["stage"] for s in report["states"]] == [
        "source",
        "release-and-operands",
        "control-plane:1.34",
        "node-group:worker:1.34",
        "final",
    ]
    assert report["states"][2]["node_versions"] == {"worker": "1.33"}
    assert report["transitions"][0]["outcome"] == "pass"


def test_target_compatible_chart_cannot_roll_out_on_incompatible_source():
    matrix = load_matrix()
    with pytest.raises(ValueError, match="Helm rejected"):
        assess_upgrade_states(
            manifest(matrix, distribution="unknown"),
            manifest(matrix, "26.1.0", "1.34", distribution="unknown", constraint=">=1.34.0"),
            intent(),
        )


@pytest.mark.parametrize("constraint,admitted", [(">=1.35.0", True), (">=1.36.0", False)])
def test_chart_constraints_use_control_plane_during_supported_node_skew(constraint, admitted):
    matrix = load_matrix()
    snapshot = manifest(matrix, kubernetes="1.35", distribution="unknown", constraint=constraint)
    transition = intent("1.35", "1.35", ())
    transition.node_groups[0].source_version = "1.34"
    if not admitted:
        with pytest.raises(ValueError, match="Helm rejected"):
            assess_upgrade_states(snapshot, snapshot, transition)
        return
    report = assess_upgrade_states(snapshot, snapshot, transition)
    assert report["states"][0]["node_versions"] == {"worker": "1.34"}
    assert {
        finding["kubernetes_version"]
        for state in report["states"]
        for finding in state["findings"]
        if "constraint" in finding
    } == {"1.35"}


def test_source_only_remediation_requires_exact_review_and_cannot_excuse_later_states():
    matrix = load_matrix()
    source = manifest(matrix, kubernetes="1.34")
    target = manifest(matrix, "25.10.0", "1.34")
    transition = intent("1.34", "1.34", ())
    with pytest.raises(ValueError, match="Outside documented"):
        assess_upgrade_states(source, target, transition)
    matrix["reviewed_remediations"] = [
        {
            "id": "reviewed-network-repair",
            "distribution": "nvidia-upstream-network",
            "source": {"application_version": "25.7.0", "kubernetes_minor": "1.34"},
            "target": {"application_version": "25.10.0", "kubernetes_minor": "1.34"},
            "assertions": ["nvidia-network-25-7-kubernetes"],
            "evidence": ["nvidia-network-upgrades"],
        }
    ]
    report = assess_upgrade_states(source, target, transition)
    assert any(f.get("original_outcome") == "block" for f in report["states"][0]["findings"])
    with pytest.raises(ValueError, match="Outside documented"):
        assess_upgrade_states(source, manifest(matrix, "25.7.0", "1.34"), transition)


def test_nonsequential_minor_hop_is_never_admitted():
    matrix = load_matrix()
    with pytest.raises(ValueError, match="sequential"):
        assess_upgrade_states(
            manifest(matrix, distribution="unknown"),
            manifest(matrix, kubernetes="1.35", distribution="unknown"),
            intent(target="1.35", hops=("1.35",)),
        )
