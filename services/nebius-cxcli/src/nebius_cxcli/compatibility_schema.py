"""Closed data schema for compatibility policy; no executable expressions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from urllib.parse import urlsplit

ADAPTER_SELECTORS = {
    "coverage": "all_selected_components",
    "exact_artifact_kube_version": "all_selected_helm_artifacts",
    "resolved_terraform_constraints": "all_selected_terraform_modules",
    "nebius_node_group_compatibility_api": "all_selected_mk8s_node_groups",
    "nebius_cluster_version_api": "all_selected_mk8s_targets",
    "frozen_soperator_release_contract": "all_selected_soperator_targets",
    "gpu_network_driver_and_nfd_ownership": "gpu_enabled_targets",
    "artifact_bound_operand_constraints": "enabled_operator_operands",
}


def _shape(value, required, optional=()):
    if (
        not isinstance(value, Mapping)
        or not set(required) <= value.keys()
        or set(value) - set(required) - set(optional)
    ):
        raise ValueError(f"Malformed compatibility fields; expected {sorted(required)}")


def _strings(value, *, empty=False):
    if (
        not isinstance(value, list)
        or (not value and not empty)
        or any(not isinstance(x, str) or not x for x in value)
        or len(value) != len(set(value))
    ):
        raise ValueError("Compatibility list requires unique nonempty strings")


def _date(value):
    if not isinstance(value, str):
        raise ValueError("Compatibility date must be an ISO date string")
    date.fromisoformat(value)


def stable_version(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+(?:\.\d+)?", value):
        raise ValueError(f"Malformed or non-stable compatibility version: {value!r}")
    return tuple(int(part) for part in value.removeprefix("v").split("."))


def kubernetes_minor(value: str) -> str:
    if not value:
        return ""
    # Preserve the full provider version separately; only minor-scoped rules
    # use this normalized identity. Helm still receives the original version.
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?", value)
    if not match:
        raise ValueError("Malformed Kubernetes version")
    return f"{int(match[1])}.{int(match[2])}"


def validate_closed_schema(matrix):
    _shape(
        matrix,
        {
            "schema_version",
            "status",
            "reviewed_on",
            "coverage",
            "selection",
            "version_sets",
            "identity",
            "distributions",
            "sources",
            "assertions",
            "required_checks",
            "transitions",
            "reviewed_remediations",
            "policy",
            "freeze",
            "report",
        },
    )
    _date(matrix["reviewed_on"])
    for key in ("version_sets", "distributions", "sources"):
        if not isinstance(matrix[key], Mapping):
            raise ValueError(f"Compatibility {key} must be a mapping")
    for value in matrix["distributions"].values():
        _shape(value, {"component_id", "source"})
        if not all(isinstance(x, str) and x for x in value.values()):
            raise ValueError("Invalid distribution identity")
    for value in matrix["sources"].values():
        _shape(value, {"authority", "kind", "url", "checked_on"}, {"expires_on", "release"})
        _date(value["checked_on"])
        if "expires_on" in value:
            _date(value["expires_on"])
        if (
            not isinstance(value["url"], str)
            or urlsplit(value["url"]).scheme != "https"
            or not urlsplit(value["url"]).netloc
        ):
            raise ValueError("Evidence requires an HTTPS source URL")
    for key in ("assertions", "required_checks", "transitions"):
        if not isinstance(matrix[key], list):
            raise ValueError(f"Compatibility {key} must be a list")
    for row in matrix["assertions"]:
        _shape(row, {"id", "subject", "claim", "evidence"}, {"when"})
        _shape(row["subject"], {"distribution"}, {"release", "application_version"})
        if row["subject"]["distribution"] not in matrix["distributions"]:
            raise ValueError("Unknown assertion distribution")
        app = row["subject"].get("application_version")
        if app is not None:
            _shape(app, {"scheme", "family"})
            if app["scheme"] != "nvidia_calendar" or len(stable_version(app["family"])) != 2:
                raise ValueError("Invalid application version family")
        if "release" in row["subject"]:
            stable_version(row["subject"]["release"])
        when = row.get("when", {})
        _shape(when, set(), {"kubernetes_minor"})
        if (
            "kubernetes_minor" in when
            and kubernetes_minor(when["kubernetes_minor"]) != when["kubernetes_minor"]
        ):
            raise ValueError("Assertion when requires a Kubernetes minor")
        claim = row["claim"]
        kind = claim.get("kind") if isinstance(claim, Mapping) else None
        fields = {
            "recommended_version_set": "version_set",
            "supported_kubernetes_minors": "versions",
            "minimum_supported_application_version": "version",
            "minimum_kubernetes_minor": "version",
        }
        if kind not in fields:
            raise ValueError("Unknown compatibility claim")
        _shape(claim, {"kind", fields[kind]})
        if kind == "recommended_version_set":
            if claim["version_set"] not in matrix["version_sets"]:
                raise ValueError("Unknown recommended version set")
        elif kind == "supported_kubernetes_minors":
            _strings(claim["versions"])
            if any(kubernetes_minor(v) != v for v in claim["versions"]):
                raise ValueError("Support sets require Kubernetes minors")
        else:
            stable_version(claim["version"])
            if kind == "minimum_supported_application_version" and app is None:
                raise ValueError("Application minimum requires an application identity")
        _evidence(row, matrix)
    ids = set()
    for row in matrix["required_checks"]:
        _shape(
            row,
            {"id", "select", "evaluator"},
            {
                "absent_optional_constraint",
                "unreadable_or_malformed_metadata",
                "constraint_not_satisfied",
                "not_declared_implies_support",
            },
        )
        if ADAPTER_SELECTORS.get(row["evaluator"]) != row["select"] or row["evaluator"] in ids:
            raise ValueError("Unknown or duplicate compatibility adapter selector")
        ids.add(row["evaluator"])
        settings = {
            key: value for key, value in row.items() if key not in {"id", "select", "evaluator"}
        }
        expected = {
            "absent_optional_constraint": "not_declared",
            "unreadable_or_malformed_metadata": "block",
            "constraint_not_satisfied": "block",
            "not_declared_implies_support": False,
        }
        if settings and (row["evaluator"] != "exact_artifact_kube_version" or settings != expected):
            raise ValueError("Unsupported hard adapter policy")
    if ids != set(ADAPTER_SELECTORS):
        raise ValueError("Registry is missing required compatibility adapters")
    selection = matrix["selection"]
    _shape(
        selection,
        {
            "config_reference",
            "profile_defaults",
            "default_applies_when",
            "version_sets_enable_components",
            "persist_resolved_reference_in_config",
            "ambiguous_or_missing_required_selection",
            "explicit_version_conflicts_with_set",
            "custom_selection",
            "custom_selection_must_pass_all_applicable_rules",
        },
    )
    _shape(selection["profile_defaults"], {"mk8s", "soperator"})
    for profile, name in selection["profile_defaults"].items():
        if (
            name not in matrix["version_sets"]
            or profile not in matrix["version_sets"][name]["profiles"]
        ):
            raise ValueError("Invalid profile default version set")
    for row in matrix["version_sets"].values():
        _shape(
            row,
            {
                "profiles",
                "qualification",
                "pins",
                "evidence",
                "requires",
                "supported_kubernetes_minors",
            },
        )
        _strings(row["profiles"])
        if not set(row["profiles"]) <= {"mk8s", "soperator"} or not isinstance(
            row["pins"], Mapping
        ):
            raise ValueError("Invalid version set profile or pins")
        _evidence(row, matrix)
        for pin in row["pins"].values():
            _shape(pin, {"distribution", "chart_version"}, {"observed_artifact"})
            stable_version(pin["chart_version"])
            if "observed_artifact" in pin:
                artifact = pin["observed_artifact"]
                _shape(
                    artifact,
                    {
                        "checked_on",
                        "chart_app_version",
                        "kube_version_constraint",
                        "package_sha256",
                        "oci_manifest_digest",
                    },
                    {"controller_image"},
                )
                _date(artifact["checked_on"])
                for field in ("package_sha256", "oci_manifest_digest"):
                    if not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact[field]):
                        raise ValueError("Malformed observed artifact digest")
                if "controller_image" in artifact:
                    _shape(
                        artifact["controller_image"],
                        {
                            "declared_reference",
                            "observed_runtime_digest",
                            "verified_binary_version",
                            "nvidia_upstream_equivalence",
                        },
                    )
    transition_fields = {
        "kubernetes_minor_step_and_version_skew": {"component", "before", "after", "evidence"},
        "same_or_next_published_release_family": {
            "distribution",
            "version_scheme",
            "allow_within_family",
            "reviewed_family_edges",
            "unlisted_family_edge",
            "evidence",
        },
        "all_required_checks_per_stage": {"includes"},
        None: {"when", "before", "after", "evidence"},
    }
    ids = set()
    for row in matrix["transitions"]:
        evaluator = row.get("evaluator")
        if evaluator not in transition_fields or row.get("id") in ids:
            raise ValueError("Unknown or duplicate transition rule")
        ids.add(row.get("id"))
        _shape(
            row,
            {"id", "operations"}
            | transition_fields[evaluator]
            | ({"evaluator"} if evaluator else set()),
        )
        _strings(row["operations"])
        if row["operations"] != (["install"] if evaluator is None else ["upgrade"]):
            raise ValueError("Invalid transition operation")
        if "evidence" in row:
            _evidence(row, matrix)
        if evaluator is None and (
            row["when"] != {"rdma": True}
            or row["before"] != "nvidia-network-operator"
            or row["after"] != "nvidia-gpu-operator"
        ):
            raise ValueError("Unsupported install ordering rule")
        if evaluator == "kubernetes_minor_step_and_version_skew" and (
            row["component"],
            row["before"],
            row["after"],
        ) != ("mk8s", "control_plane", "node_groups"):
            raise ValueError("Unsupported Kubernetes transition rule")
        if evaluator == "same_or_next_published_release_family":
            if (
                row["distribution"] not in matrix["distributions"]
                or row["version_scheme"] != "nvidia_calendar"
                or row["allow_within_family"] is not True
                or row["unlisted_family_edge"] != "unknown"
            ):
                raise ValueError("Invalid operator transition rule")
            for edge in row["reviewed_family_edges"]:
                _shape(edge, {"from", "to"})
                if any(len(stable_version(v)) != 2 for v in edge.values()):
                    raise ValueError("Invalid transition family")
        if evaluator == "all_required_checks_per_stage" and row["includes"] != [
            "source",
            "target",
            "mixed_node_versions",
            "operand_rollout",
            "crd_transition",
        ]:
            raise ValueError("Unsupported intermediate state coverage")
    for field, keys in {
        "coverage": {
            "inventory",
            "initial_profiles",
            "unassessed_components",
            "disabled_components",
            "upstream_children",
        },
        "identity": {"required", "artifact_fields", "context_fields"},
        "freeze": {
            "bind",
            "recovery",
            "live_rechecks",
            "live_recheck_may_change_selected_versions",
            "current_matrix_may_replace_admitted_matrix",
        },
        "report": {
            "separate_axes",
            "row_fields",
            "runtime_receipts",
            "public_catalog_contains_live_environment_data",
        },
    }.items():
        _shape(matrix[field], keys)
    _fixed_semantics(matrix)
    if not isinstance(matrix["reviewed_remediations"], list):
        raise ValueError("Reviewed remediations must be a list")
    remediation_ids = set()
    for row in matrix["reviewed_remediations"]:
        _shape(row, {"id", "distribution", "source", "target", "assertions", "evidence"})
        if row["id"] in remediation_ids or row["distribution"] not in matrix["distributions"]:
            raise ValueError("Duplicate or unknown reviewed remediation")
        remediation_ids.add(row["id"])
        for key in ("source", "target"):
            _shape(
                row[key], {"kubernetes_minor"}, {"chart_version", "application_version", "release"}
            )
            if (
                len(row[key]) < 2
                or kubernetes_minor(row[key]["kubernetes_minor"]) != row[key]["kubernetes_minor"]
            ):
                raise ValueError("Remediation requires exact artifact and Kubernetes versions")
            for version in row[key].values():
                stable_version(version)
        _strings(row["assertions"])
        if set(row["assertions"]) - {a["id"] for a in matrix["assertions"]}:
            raise ValueError("Remediation references unknown assertions")
        _evidence(row, matrix)


def _fixed_semantics(matrix):
    """Documented evaluator settings are closed, not silently ignored knobs."""
    expected = {
        "coverage": {
            "inventory": "resolved_selected_components",
            "initial_profiles": ["mk8s", "soperator"],
            "unassessed_components": "report_unknown",
            "disabled_components": "not_applicable",
            "upstream_children": "expand_from_frozen_release",
        },
        "selection": {
            "config_reference": "compatibility.targets.<target_id>.version_set",
            "default_applies_when": "gpu_enabled",
            "version_sets_enable_components": False,
            "persist_resolved_reference_in_config": True,
            "ambiguous_or_missing_required_selection": "reject",
            "explicit_version_conflicts_with_set": "reject",
            "custom_selection": "explicit_versions_without_version_set",
            "custom_selection_must_pass_all_applicable_rules": True,
        },
        "freeze": {
            "recovery": "reuse_admitted_snapshot",
            "live_rechecks": [
                "identity",
                "artifact_integrity",
                "provider_admission",
                "available_capacity",
            ],
            "live_recheck_may_change_selected_versions": False,
            "current_matrix_may_replace_admitted_matrix": False,
        },
        "report": {
            "separate_axes": ["constraints", "documented_support", "runtime_validation"],
            "row_fields": [
                "check_id",
                "subject",
                "dependency",
                "applicability",
                "outcome",
                "evidence",
                "reason",
            ],
            "runtime_receipts": "operation_owned",
            "public_catalog_contains_live_environment_data": False,
        },
    }
    for section, fields in expected.items():
        if any(
            matrix[section].get(k) != v or type(matrix[section].get(k)) is not type(v)
            for k, v in fields.items()
        ):
            raise ValueError(f"Unsupported compatibility {section} semantics")
    for section, keys in {
        "identity": ("required", "artifact_fields", "context_fields"),
        "freeze": ("bind",),
    }.items():
        for key in keys:
            _strings(matrix[section][key])


def _evidence(row, matrix):
    _strings(row["evidence"])
    if any(key not in matrix["sources"] for key in row["evidence"]):
        raise ValueError("Missing compatibility source evidence")


def required_adapters(subject, matrix):
    """Translate the closed registry selectors into applicable hard adapters."""
    selected = {
        "all_selected_components": False,  # intrinsic inventory coverage, no external receipt
        "all_selected_helm_artifacts": subject.get("kind") == "helm",
        "all_selected_terraform_modules": subject.get("kind") == "terraform",
        "all_selected_mk8s_node_groups": subject.get("component_id") == "mk8s",
        "all_selected_mk8s_targets": subject.get("component_id") == "mk8s",
        "all_selected_soperator_targets": subject.get("component_id") == "soperator",
        "gpu_enabled_targets": subject.get("component_id") == "mk8s"
        and subject.get("gpu_enabled") is True,
        "enabled_operator_operands": subject.get("kind") == "operand",
    }
    return sorted(row["evaluator"] for row in matrix["required_checks"] if selected[row["select"]])
