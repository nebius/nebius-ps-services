"""Assess deterministic upgrade stages without selecting replacement versions."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import date
from typing import Any

from .compatibility_adapters import helm_constraint
from .compatibility_matrix import assess, digest, require_admitted, validate_matrix
from .compatibility_schema import kubernetes_minor, stable_version


def assess_upgrade_states(
    source: Mapping, target: Mapping, intent, *, nodes_each_hop: bool = True
) -> dict:
    """Cover source, release/operand rollout, and every mixed-node minor hop.

    Provider tuple and Terraform transition admission remain at their existing
    native planners. This report binds those frozen planner inputs and adds
    support/artifact checks for every version exposed during that plan.
    """
    matrix = target["render"]["inputs"]["matrix"]
    validate_matrix(matrix)
    snapshots = {
        "source": source["render"]["compatibility"],
        "target": target["render"]["compatibility"],
    }
    states = []
    nodes = {group.key: group.source_version for group in intent.node_groups}
    cp = intent.source_kubernetes_version

    def inspect(label, snapshot, control_plane, node_versions):
        original = snapshots[snapshot]
        receipts = {
            row["subject_sha256"]: row
            for row in original["receipts"]
            if row["evaluator"] == "exact_artifact_kube_version"
        }
        findings = []
        for version in sorted({control_plane, *node_versions.values()}):
            inventory = copy.deepcopy(original["inventory"])
            for item in inventory:
                if item["instance_id"].split("/", 1)[0] != intent.target_ref:
                    continue
                old_identity = digest(item)
                item["kubernetes_version"] = version
                item["kubernetes_minor"] = kubernetes_minor(version)
                # Helm constrains the API server used to render/install charts,
                # not kubelets that legitimately lag during a rolling upgrade.
                if (
                    version == control_plane
                    and item.get("enabled", True)
                    and item["kind"] == "helm"
                ):
                    artifact = receipts.get(old_identity, {}).get("artifact")
                    if not isinstance(artifact, Mapping) or not isinstance(
                        artifact.get("metadata"), Mapping
                    ):
                        raise ValueError(
                            "Upgrade is missing frozen chart constraints; rerender required"
                        )
                    result = helm_constraint(artifact["metadata"], version)
                    for dependency in artifact.get("enabled_dependencies", []):
                        helm_constraint(dependency["metadata"], version)
                    findings.append(
                        {
                            "subject_sha256": old_identity,
                            "kubernetes_version": version,
                            "constraint": result,
                        }
                    )
            report = assess(
                inventory,
                matrix=matrix,
                today=date.fromisoformat(original["report"]["evaluated_on"]),
            )
            if label == "source":
                for finding in report["rows"]:
                    if finding["outcome"] != "block" or finding["axis"] != "documented_support":
                        continue
                    before = finding["subject"]
                    after: dict[str, Any] = next(
                        (
                            item
                            for item in snapshots["target"]["inventory"]
                            if item["instance_id"] == before["instance_id"]
                            and item["component_id"] == before["component_id"]
                        ),
                        {},
                    )
                    reviews = [
                        review
                        for review in matrix["reviewed_remediations"]
                        if review["distribution"]
                        == before["distribution"]
                        == after.get("distribution")
                        and finding["check_id"] in review["assertions"]
                        and all(before.get(k) == v for k, v in review["source"].items())
                        and all(after.get(k) == v for k, v in review["target"].items())
                    ]
                    if len(reviews) == 1:
                        finding.update(
                            outcome="warn",
                            original_outcome="block",
                            remediation=reviews[0],
                            reason="Reviewed source-only remediation; all subsequent states must pass",
                        )
            require_admitted(report)
            findings.extend(row for row in report["rows"] if row["axis"] == "documented_support")
        states.append(
            {
                "stage": label,
                "control_plane": control_plane,
                "node_versions": dict(node_versions),
                "findings": findings,
            }
        )

    inspect("source", "source", cp, nodes)
    # New releases and ordinary operands may roll out before a control-plane hop.
    inspect("release-and-operands", "target", cp, nodes)
    if nodes_each_hop:
        for key in sorted(nodes):
            if nodes[key] != cp:
                nodes[key] = cp
                inspect("catch-up-node-group:" + key + ":" + cp, "target", cp, nodes)
    for version in intent.kubernetes_hops:
        before, after = (
            stable_version(kubernetes_minor(cp)),
            stable_version(kubernetes_minor(version)),
        )
        if after != (before[0], before[1] + 1):
            raise ValueError("Kubernetes upgrades require sequential minor transitions")
        cp = version
        inspect("control-plane:" + cp, "target", cp, nodes)
        if nodes_each_hop:
            for key in sorted(nodes):
                nodes[key] = cp
                inspect("node-group:" + key + ":" + cp, "target", cp, nodes)
    if not nodes_each_hop:
        for group in intent.node_groups:
            if nodes[group.key] != group.target_version:
                nodes[group.key] = group.target_version
                inspect("node-group:" + group.key + ":" + group.target_version, "target", cp, nodes)
    inspect(
        "final",
        "target",
        intent.target_kubernetes_version,
        {group.key: group.target_version for group in intent.node_groups},
    )
    transitions = assess_operator_transitions(snapshots["source"], snapshots["target"], matrix)
    payload = {
        "schema": "nebius-cxcli.compatibility-transitions/v1",
        "matrix_sha256": digest(matrix),
        "source_sha256": snapshots["source"]["sha256"],
        "target_sha256": snapshots["target"]["sha256"],
        "provider_rows_sha256": digest([vars(row) for row in intent.compatibility_rows]),
        "states": states,
        "transitions": transitions,
    }
    return {**payload, "sha256": digest(payload)}


def assess_node_template_plan(manifest: Mapping, plan) -> dict:
    """Use the generic planner's actual control-plane-first rollout order."""
    from types import SimpleNamespace

    selected = {group.name for group in plan.node_groups}
    groups = tuple(
        SimpleNamespace(
            key=group.name,
            source_version=group.version,
            target_version=plan.target_version if group.name in selected else group.version,
        )
        for group in plan.all_node_groups
    )
    intent = SimpleNamespace(
        target_ref=plan.target.instance_id,
        node_groups=groups,
        source_kubernetes_version=plan.current_version,
        target_kubernetes_version=plan.target_version,
        kubernetes_hops=tuple(hop.to_version for hop in plan.hops),
        compatibility_rows=plan.compatibility_matrix,
    )
    return assess_upgrade_states(manifest, manifest, intent, nodes_each_hop=False)


def assess_operator_transitions(source: Mapping, target: Mapping, matrix: Mapping) -> list[dict]:
    """Shared operator edges for chart-only and coordinated platform upgrades."""
    transitions = []
    source_rows = {(row["component_id"], row["instance_id"]): row for row in source["inventory"]}
    for row in target["inventory"]:
        old = source_rows.get((row["component_id"], row["instance_id"]))
        if not old or not row.get("enabled", True) or not old.get("enabled", True):
            continue
        if any(
            rule.get("distribution") == row["distribution"] for rule in matrix["transitions"]
        ) and (not old.get("application_version") or not row.get("application_version")):
            raise ValueError("Operator transition is missing required application version evidence")
        if not old.get("application_version") or not row.get("application_version"):
            continue
        for rule in matrix["transitions"]:
            if (
                rule.get("distribution") != row["distribution"]
                or old["distribution"] != row["distribution"]
            ):
                continue
            before = stable_version(old["application_version"])
            after = stable_version(row["application_version"])
            if after < before:
                raise ValueError("Operator downgrade has no supported transition adapter")
            edge = {"from": ".".join(map(str, before[:2])), "to": ".".join(map(str, after[:2]))}
            known = before[:2] == after[:2] or edge in rule["reviewed_family_edges"]
            transitions.append(
                {
                    "rule": rule["id"],
                    "edge": edge,
                    "outcome": "pass" if known else "warn",
                    "reason": "Reviewed release-family transition"
                    if known
                    else "Release-family transition support is unknown",
                }
            )
    return transitions


def assess_chart_upgrade(source: Mapping, target: Mapping, *, target_ref: str) -> dict:
    """Assess chart rollout on the unchanged target Kubernetes platform."""
    matrix = target["render"]["inputs"]["matrix"]
    validate_matrix(matrix)
    snapshots = {
        label: {
            **manifest["render"]["compatibility"],
            "inventory": [
                row
                for row in manifest["render"]["compatibility"]["inventory"]
                if row["instance_id"].split("/", 1)[0] == target_ref and row["kind"] != "terraform"
            ],
        }
        for label, manifest in (("source", source), ("target", target))
    }
    transitions = assess_operator_transitions(snapshots["source"], snapshots["target"], matrix)
    states = []
    for label, snapshot in snapshots.items():
        report = assess(snapshot["inventory"], matrix=matrix, receipts=snapshot["receipts"])
        # Source exceptions are allowed only through the same explicitly reviewed
        # source-to-target remediation used by coordinated platform upgrades.
        if label == "source":
            for finding in report["rows"]:
                if finding["outcome"] != "block" or finding["axis"] != "documented_support":
                    continue
                before = finding["subject"]
                after: dict[str, Any] = next(
                    (
                        row
                        for row in snapshots["target"]["inventory"]
                        if row["component_id"] == before["component_id"]
                        and row["instance_id"] == before["instance_id"]
                    ),
                    {},
                )
                reviews = [
                    review
                    for review in matrix["reviewed_remediations"]
                    if review["distribution"] == before["distribution"] == after.get("distribution")
                    and finding["check_id"] in review["assertions"]
                    and all(before.get(k) == v for k, v in review["source"].items())
                    and all(after.get(k) == v for k, v in review["target"].items())
                ]
                if len(reviews) == 1:
                    finding.update(
                        outcome="warn",
                        original_outcome="block",
                        remediation=reviews[0],
                        reason="Reviewed source-only remediation; target must pass",
                    )
        require_admitted(report)
        states.append({"stage": label, "report": report})
    payload = {
        "schema": "nebius-cxcli.chart-transition/v1",
        "matrix_sha256": digest(matrix),
        "source_sha256": snapshots["source"]["sha256"],
        "target_sha256": snapshots["target"]["sha256"],
        "target_ref": target_ref,
        "states": states,
        "transitions": transitions,
    }
    return {**payload, "sha256": digest(payload)}
