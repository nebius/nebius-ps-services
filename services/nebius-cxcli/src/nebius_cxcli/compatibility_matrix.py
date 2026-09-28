"""Versioned compatibility evidence; recommendations never imply vendor support.

The registry contains data, never Python expressions or commands. Hard adapters
produce scoped receipts at their actual validation boundary. A missing receipt is
pending during planning and blocking at admission.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .compatibility_schema import (
    kubernetes_minor,
    required_adapters,
    stable_version,
    validate_closed_schema,
)

EVALUATOR_VERSION = "nebius-cxcli.compatibility/v1"
MATRIX_FILE = "compatibility-matrix.yaml"
FROZEN_MATRIX: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "compatibility_matrix", default=None
)
_CHECKS = frozenset(
    {
        "coverage",
        "exact_artifact_kube_version",
        "resolved_terraform_constraints",
        "nebius_node_group_compatibility_api",
        "nebius_cluster_version_api",
        "frozen_soperator_release_contract",
        "gpu_network_driver_and_nfd_ownership",
        "artifact_bound_operand_constraints",
    }
)
_CLAIMS = frozenset(
    {
        "recommended_version_set",
        "supported_kubernetes_minors",
        "minimum_supported_application_version",
        "minimum_kubernetes_minor",
    }
)


def digest(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key in result:
            raise ValueError("Compatibility matrix contains a duplicate or non-string key")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def validate_matrix(matrix: Mapping[str, Any]) -> None:
    validate_closed_schema(matrix)
    if matrix.get("schema_version") != 1 or matrix.get("status") != "active":
        raise ValueError("Unsupported compatibility matrix schema/status")
    for field in ("version_sets", "distributions", "sources", "policy", "selection"):
        if not isinstance(matrix.get(field), Mapping):
            raise ValueError(f"Compatibility matrix {field} must be a mapping")
    policy = matrix["policy"]
    if policy != {
        "constraint_failure": "block",
        "required_evidence_missing": "block",
        "conflicting_evidence": "block",
        "outside_documented_support": "block",
        "unknown_support": "warn",
        "expired_positive_evidence": "warn",
        "recommendation_implies_support": False,
        "runtime_success_implies_vendor_support": False,
        "cross_distribution_inheritance": "require_explicit_verified_mapping",
    }:
        raise ValueError("Unsupported compatibility enforcement policy")
    ids: set[str] = set()
    for row in matrix.get("assertions", []):
        if not isinstance(row, Mapping) or not row.get("id") or row["id"] in ids:
            raise ValueError("Compatibility assertions require unique IDs")
        ids.add(row["id"])
        if row.get("claim", {}).get("kind") not in _CLAIMS:
            raise ValueError(f"Unknown compatibility claim in {row['id']}")
        if not row.get("evidence") or any(e not in matrix["sources"] for e in row["evidence"]):
            raise ValueError(f"Missing source evidence for {row['id']}")
        for key in row.get("subject", {}):
            if key not in {"distribution", "release", "application_version"}:
                raise ValueError(f"Unsupported assertion selector: {key}")
    ids.clear()
    for row in matrix.get("required_checks", []):
        if row.get("evaluator") not in _CHECKS or not row.get("id") or row["id"] in ids:
            raise ValueError("Unknown or duplicate required compatibility adapter")
        ids.add(row["id"])
    for name, version_set in matrix["version_sets"].items():
        for component, pin in version_set.get("pins", {}).items():
            distribution = matrix["distributions"].get(pin.get("distribution"), {})
            if distribution.get("component_id") != component or not pin.get("chart_version"):
                raise ValueError(f"Invalid exact pin in version set {name}")


def load_matrix(path: Path | None = None) -> dict[str, Any]:
    frozen = FROZEN_MATRIX.get()
    if frozen is not None:
        if path is not None:
            raise ValueError("A matrix path cannot replace frozen operation inputs")
        return copy.deepcopy(dict(frozen))
    if path is None:
        local = Path(__file__).resolve().parents[2] / MATRIX_FILE
        text = (
            local.read_text()
            if local.is_file()
            else resources.files(__package__).joinpath(MATRIX_FILE).read_text()
        )
    else:
        text = path.read_text()
    matrix = yaml.load(text, Loader=_UniqueLoader)
    if not isinstance(matrix, dict):
        raise ValueError("Compatibility matrix must be a mapping")
    validate_matrix(matrix)
    return matrix


def validate_selection(payload: Mapping[str, Any]) -> None:
    selection = payload.get("compatibility", {})
    if not isinstance(selection, Mapping) or set(selection) - {"targets"}:
        raise ValueError("compatibility supports only targets")
    targets = selection.get("targets", {})
    if not isinstance(targets, Mapping):
        raise ValueError("compatibility.targets must be a mapping")
    declared = {
        str(row.get("instance_id", row.get("id", "")))
        for row in [
            *payload.get("deploy", {}).get("targets", []),
            *payload.get("infra", {}).get("components", []),
        ]
        if isinstance(row, Mapping)
    }
    for target, choice in targets.items():
        if target not in declared:
            raise ValueError(f"Unknown compatibility target: {target}")
        if not isinstance(choice, Mapping) or set(choice) != {"version_set"}:
            raise ValueError("Each compatibility target requires only version_set")
        if choice["version_set"] is not None and not isinstance(choice["version_set"], str):
            raise ValueError("version_set must be a name or null for explicit custom versions")


def default_chart_version(
    component: str,
    *,
    source: str,
    matrix: Mapping | None = None,
    profile: str | None = None,
    version_set: str | None = None,
) -> str | None:
    """Default pins apply only to the exact distribution, never an upstream lookalike."""
    matrix = matrix or load_matrix()
    sets = (
        {version_set}
        if version_set
        else {matrix["selection"]["profile_defaults"][profile]}
        if profile
        else set(matrix["selection"]["profile_defaults"].values())
    )
    versions = {
        pin["chart_version"]
        for name in sets
        for key, pin in matrix["version_sets"][name]["pins"].items()
        if key == component
        and matrix["distributions"][pin["distribution"]]["source"] == source.rstrip("/")
    }
    if len(versions) > 1:
        # A catalog has no target identity. Defer differing profile defaults to
        # selected_inventory; an unrelated profile cannot break catalog loading.
        return None
    return next(iter(versions), None)


def _version(value: str) -> tuple[int, ...]:
    return stable_version(value)


def _matches(subject: Mapping, row: Mapping) -> bool:
    for key, expected in subject.items():
        actual = row.get(key)
        if key == "application_version":
            if expected.get("scheme") != "nvidia_calendar":
                raise ValueError("Unknown application version scheme")
            if not actual or _version(actual)[:2] != _version(expected["family"]):
                return False
        elif actual != expected:
            return False
    return True


def assess(
    inventory: Sequence[Mapping[str, Any]],
    *,
    matrix: Mapping[str, Any] | None = None,
    receipts: Sequence[Mapping[str, Any]] = (),
    admission: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Assess each exact subject independently on three separate evidence axes."""
    matrix = matrix or load_matrix()
    validate_matrix(matrix)
    today = today or date.today()
    rows: list[dict[str, Any]] = []
    receipt_index = {}
    for receipt in receipts:
        key = (receipt.get("subject_sha256"), receipt.get("evaluator"))
        if key in receipt_index or receipt.get("evaluator") not in _CHECKS:
            raise ValueError("Ambiguous or unknown compatibility receipt")
        receipt_index[key] = receipt
    identities: set[str] = set()
    for subject in inventory:
        identity = digest(subject)
        if identity in identities:
            raise ValueError("Duplicate compatibility subject")
        identities.add(identity)
        enabled = subject.get("enabled", True)

        def record(
            check: str,
            axis: str,
            outcome: str,
            reason: str,
            evidence: Any = (),
            subject: Mapping = subject,
            identity: str = identity,
            enabled: bool = enabled,
        ) -> None:
            rows.append(
                {
                    "check_id": check,
                    "subject": dict(subject),
                    "subject_sha256": identity,
                    "axis": axis,
                    "dependency": subject.get("parent_sha256"),
                    "applicability": "applicable" if enabled else "disabled",
                    "outcome": outcome,
                    "reason": reason,
                    "evidence": list(evidence),
                }
            )

        if not enabled:
            record("coverage", "constraints", "not_applicable", "Component is disabled")
            continue
        support = False
        k8s = kubernetes_minor(str(subject.get("kubernetes_minor", "")))
        for assertion in matrix.get("assertions", []):
            if not _matches(assertion["subject"], subject):
                continue
            if any(
                (k8s if k == "kubernetes_minor" else subject.get(k)) != v
                for k, v in assertion.get("when", {}).items()
            ):
                continue
            claim = assertion["claim"]
            kind = claim["kind"]
            evidence = [matrix["sources"][key] for key in assertion["evidence"]]
            if kind == "recommended_version_set":
                record(
                    assertion["id"],
                    "documented_support",
                    "recommendation",
                    "Distribution recommendation; no Kubernetes support assertion",
                    evidence,
                )
                continue
            if not k8s:
                continue
            supported = True
            if kind == "supported_kubernetes_minors":
                supported = k8s in claim["versions"]
            elif kind == "minimum_kubernetes_minor":
                supported = _version(k8s) >= _version(claim["version"])
            elif kind == "minimum_supported_application_version":
                supported = _version(subject["application_version"]) >= _version(claim["version"])
            expired = any(
                e.get("expires_on") and date.fromisoformat(e["expires_on"]) < today
                for e in evidence
            )
            support = support or (
                supported and not expired and kind == "supported_kubernetes_minors"
            )
            record(
                assertion["id"],
                "documented_support",
                "block" if not supported else "warn" if expired else "pass",
                "Outside documented support"
                if not supported
                else "Affirmative evidence expired"
                if expired
                else "Documented constraint satisfied",
                evidence,
            )
        if not support:
            record(
                "support-coverage",
                "documented_support",
                "warn",
                "Joint vendor support is unknown; runtime success cannot establish it",
            )
        for evaluator in sorted(
            set(subject.get("required_adapters", [])) | set(required_adapters(subject, matrix))
        ):
            if evaluator not in _CHECKS:
                raise ValueError(f"Unknown required compatibility adapter: {evaluator}")
            matching_receipt = receipt_index.get((identity, evaluator))
            if matching_receipt is None:
                record(
                    evaluator,
                    "constraints",
                    "block" if admission else "pending",
                    "Required adapter evidence is missing",
                )
            else:
                status = matching_receipt.get("outcome")
                if status not in {"pass", "block", "not_declared"} or not matching_receipt.get(
                    "input_sha256"
                ):
                    raise ValueError("Malformed hard compatibility matching_receipt")
                record(
                    evaluator,
                    "constraints",
                    status,
                    str(matching_receipt.get("reason", "")),
                    [matching_receipt],
                )
        record(
            "runtime-validation",
            "runtime_validation",
            "not_run",
            "Runtime validation is recorded by the operation, independently of support",
        )
    report: dict[str, Any] = {
        "schema": EVALUATOR_VERSION,
        "evaluated_on": today.isoformat(),
        "matrix_sha256": digest(matrix),
        "inventory_sha256": digest(inventory),
        "rows": rows,
    }
    report["admitted"] = admission and not any(
        row["outcome"] in {"block", "pending"} for row in rows
    )
    return report


def require_admitted(report: Mapping[str, Any]) -> None:
    blocked = [row for row in report["rows"] if row["outcome"] == "block"]
    if blocked:
        raise ValueError(
            "Compatibility admission failed: "
            + "; ".join(f"{row['check_id']}: {row['reason']}" for row in blocked)
        )
