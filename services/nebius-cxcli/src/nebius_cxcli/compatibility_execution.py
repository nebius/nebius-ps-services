"""Freeze compatibility inputs during render and recheck hard admission at deploy."""

from __future__ import annotations

import copy
import functools
from collections.abc import Mapping
from datetime import date
from typing import Any

from .compatibility_adapters import (
    chart_evidence,
    provider_evidence,
    receipt,
    soperator_evidence,
    terraform_evidence,
)
from .compatibility_matrix import EVALUATOR_VERSION, assess, digest, load_matrix, require_admitted
from .compatibility_runtime import selected_inventory
from .component_instances import component_instance_id
from .runtime_config import to_plain_data


def frozen_manifest_inputs(function):
    @functools.wraps(function)
    def wrapped(config, paths, *args, **kwargs):
        from .compatibility_artifacts import frozen_chart_inputs
        from .frozen_catalog import use_frozen_catalog

        manifest = kwargs.get("manifest")
        if not isinstance(manifest, Mapping):
            raise ValueError("Generated compatibility manifest is required; rerender first")
        with (
            use_frozen_catalog(manifest.get("render", {}).get("inputs", {})),
            frozen_chart_inputs(
                manifest.get("render", {}).get("compatibility", {}).get("chart_inputs", {})
            ),
        ):
            validate_frozen_compatibility(
                config, manifest.get("render", {}).get("compatibility", {}), matrix=load_matrix()
            )
            return function(config, paths, *args, **kwargs)

    return wrapped


def freeze_compatibility(
    config: Any, paths: Any, *, inventory: list[dict] | None = None
) -> dict[str, Any]:
    payload = to_plain_data(config)
    matrix = load_matrix()
    inventory = selected_inventory(payload, matrix=matrix) if inventory is None else inventory
    evidence: list[dict] = []
    children: list[dict] = []
    chart_inputs: dict = {}
    charts = {
        (row["id"], component_instance_id(row)): row
        for row in payload.get("apps", {}).get("charts", [])
    }
    for subject in inventory:
        if not subject["enabled"] or subject["kind"] != "helm":
            continue
        if subject["component_id"] == "soperator":
            selected, proofs = soperator_evidence(subject, paths)
            children.extend(selected)
            evidence.extend(proofs)
        else:
            row = charts[(subject["component_id"], subject["instance_id"])]
            proof = chart_evidence(subject, {**row, "values": rendered_chart_values(paths, row)})
            chart_input = proof.pop("chart_input")
            chart_inputs[digest(chart_input["reference"])] = chart_input
            subject["chart_app_version"] = proof["artifact"]["metadata"].get("appVersion")
            controller = {
                "nvidia-upstream-gpu": "nvcr.io/nvidia/gpu-operator:",
                "nvidia-upstream-network": "nvcr.io/nvidia/cloud-native/network-operator:",
            }.get(subject["distribution"])
            images = proof["artifact"]["rendered_images"]
            versions = [
                image.removeprefix(controller)
                for image in images
                if controller and image.startswith(controller) and "@" not in image
            ]
            if len(versions) == 1:
                subject["application_version"] = versions[0]
                subject["application_version_evidence"] = "rendered-official-controller-image-tag"
            proof["subject_sha256"] = digest(subject)
            evidence.append(proof)
            for image in images:
                child = {
                    "instance_id": subject["instance_id"] + "/image/" + digest(image)[7:23],
                    "component_id": subject["component_id"] + "/operand",
                    "kind": "operand",
                    "owner": subject["owner"],
                    "distribution": "unknown",
                    "image_reference": image,
                    "parent_sha256": digest(subject),
                    "kubernetes_minor": subject["kubernetes_minor"],
                    "required_adapters": ["artifact_bound_operand_constraints"],
                }
                children.append(child)
                evidence.append(
                    receipt(
                        child,
                        "artifact_bound_operand_constraints",
                        {"artifact": proof["artifact"]["artifact_sha256"], "image": image},
                        outcome="not_declared",
                        reason="Enabled image selected by native chart render; no additional artifact-declared operand constraint",
                    )
                )
    inventory.extend(children)
    report = assess(inventory, matrix=matrix, receipts=evidence)
    require_admitted(report)
    frozen = {
        "schema": EVALUATOR_VERSION,
        "config_sha256": digest(payload),
        "matrix_sha256": digest(matrix),
        "inventory": inventory,
        "receipts": evidence,
        "chart_inputs": chart_inputs,
        "report": report,
    }
    return {**frozen, "sha256": digest(frozen)}


def preserve_compatibility_observation(fresh: Mapping, previous: Mapping) -> dict:
    """Retain an identical frozen observation after fresh admission succeeds.

    Private lifecycle rendering must not change publication identity at midnight.
    Only the observation date and its derived digest may differ; fresh support,
    expiry, configuration, source and artifact evidence remain authoritative.
    """
    require_admitted(fresh["report"])
    if previous.get("sha256") != digest(
        {key: value for key, value in previous.items() if key != "sha256"}
    ):
        raise ValueError("Frozen compatibility evidence integrity mismatch")
    observed_on = previous.get("report", {}).get("evaluated_on")
    if (
        not isinstance(observed_on, str)
        or date.fromisoformat(observed_on).isoformat() != observed_on
    ):
        raise ValueError("Frozen compatibility observation date is invalid")
    candidate = copy.deepcopy(dict(fresh))
    candidate["report"]["evaluated_on"] = observed_on
    candidate["sha256"] = digest(
        {key: value for key, value in candidate.items() if key != "sha256"}
    )
    return copy.deepcopy(dict(previous if candidate == previous else fresh))


def rendered_chart_values(paths, row: Mapping) -> dict:
    """Use renderer-owned effective values, including platform ownership defaults."""
    import yaml

    from .deploy_targets import flux_target_dir

    name = row.get("release-name") or row["id"]
    namespace = row.get("namespace")
    matches = []
    target_dir = flux_target_dir(paths, component_instance_id(row))
    for path in [
        *target_dir.glob("helmrelease-*.yaml"),
        *target_dir.glob("ordinary/helmrelease-*.yaml"),
    ]:
        for doc in yaml.safe_load_all(path.read_text()):
            if (
                isinstance(doc, Mapping)
                and doc.get("kind") == "HelmRelease"
                and doc.get("metadata", {}).get("name") == name
                and doc.get("metadata", {}).get("namespace") == namespace
            ):
                matches.append(doc)
    if len(matches) > 1:
        raise ValueError("Ambiguous rendered compatibility values")
    if matches:
        if matches[0]["spec"].get("valuesFrom"):
            raise ValueError("Compatibility cannot resolve external Helm valuesFrom inputs")
        return dict(matches[0]["spec"].get("values") or {})
    # Local charts are rendered directly by the same native renderer.
    return dict(row.get("values") or {})


def validate_frozen_compatibility(config: Any, frozen: Mapping, *, matrix: Mapping) -> None:
    if frozen.get("schema") != EVALUATOR_VERSION or frozen.get("matrix_sha256") != digest(matrix):
        raise ValueError("Frozen compatibility registry/evaluator changed; rerender required")
    if frozen.get("config_sha256") != digest(to_plain_data(config)):
        raise ValueError("Frozen compatibility configuration changed; rerender required")
    if frozen.get("sha256") != digest(
        {key: value for key, value in frozen.items() if key != "sha256"}
    ):
        raise ValueError("Frozen compatibility evidence integrity mismatch")
    from .compatibility_artifacts import frozen_chart_inputs

    with frozen_chart_inputs(frozen["chart_inputs"]):
        pass
    # Verify that no selected subject was silently removed from coverage.
    selected = selected_inventory(config, matrix=matrix)
    actual = [row for row in frozen["inventory"] if "parent_sha256" not in row]
    if len(actual) != len(selected) or any(
        any(actual_row.get(key) != value for key, value in expected.items())
        for expected, actual_row in zip(selected, actual, strict=True)
    ):
        raise ValueError("Frozen compatibility inventory differs from selected components")


def admit_compatibility(
    config: Any, paths: Any, frozen: Mapping, *, terraform_validated: bool
) -> dict:
    """Called after native Terraform validation, before any cloud/app mutations."""
    payload = to_plain_data(config)
    matrix = load_matrix()
    validate_frozen_compatibility(config, frozen, matrix=matrix)
    from .compatibility_artifacts import admit_chart_sources, frozen_chart_inputs

    admit_chart_sources(frozen["chart_inputs"])

    with frozen_chart_inputs(frozen["chart_inputs"]):
        fresh = freeze_compatibility(config, paths)
    if fresh["inventory"] != frozen["inventory"] or fresh["receipts"] != frozen["receipts"]:
        raise ValueError(
            "Selected artifact or tool compatibility inputs changed; rerender required"
        )
    evidence = list(frozen["receipts"])
    for subject in frozen["inventory"]:
        if not subject.get("enabled", True):
            continue
        if subject["kind"] == "terraform":
            if not terraform_validated:
                raise ValueError("Native Terraform compatibility validation was not performed")
            evidence.append(terraform_evidence(subject, paths.infra_dir))
        if subject["component_id"] == "mk8s":
            evidence.extend(provider_evidence(subject, payload))
        if "gpu_network_driver_and_nfd_ownership" in subject.get("required_adapters", []):
            from .mk8s_gpu import mk8s_gpu_dependency_issues

            issues = mk8s_gpu_dependency_issues(payload)
            if issues:
                raise ValueError("GPU/RDMA compatibility: " + "; ".join(issues))
            evidence.append(
                receipt(
                    subject,
                    "gpu_network_driver_and_nfd_ownership",
                    payload,
                    reason="Selected GPU, network, driver and NFD ownership validated",
                )
            )
    report = assess(frozen["inventory"], matrix=matrix, receipts=evidence, admission=True)
    require_admitted(report)
    return report


def verify_constraint_replay(admitted: Mapping, current: Mapping) -> None:
    """Provider availability is rechecked; admitted native inputs cannot drift."""
    if not admitted.get("admitted") or not current.get("admitted"):
        raise ValueError("Recovery requires complete compatibility admission reports")

    def identities(report):
        return {
            (row["subject_sha256"], row["check_id"]): row["evidence"]
            for row in report["rows"]
            if row["axis"] == "constraints"
            and row["check_id"]
            in {
                "resolved_terraform_constraints",
                "exact_artifact_kube_version",
                "frozen_soperator_release_contract",
            }
        }

    if admitted["matrix_sha256"] != current["matrix_sha256"] or identities(admitted) != identities(
        current
    ):
        raise ValueError(
            "Frozen native compatibility inputs changed; recovery cannot reinterpret them"
        )
