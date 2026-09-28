"""Frozen, application-only admission before prerequisite or cluster effects."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace

from .compatibility_adapters import receipt
from .compatibility_artifacts import admit_chart_sources, frozen_chart_inputs
from .compatibility_execution import freeze_compatibility, validate_frozen_compatibility
from .compatibility_matrix import assess, digest, load_matrix, require_admitted
from .component_instances import component_instance_id
from .deployment_state import DeploymentGeneration
from .frozen_catalog import use_frozen_catalog
from .render import reset_generated_bundle, staged_generated_paths
from .runtime_config import to_plain_data


def application_files(paths) -> dict:
    files = {}
    for path in sorted(
        [*paths.flux_dir.rglob("*"), *(paths.generated_dir / "grafana_dashboards").rglob("*")]
    ):
        if path.is_symlink() or any(
            parent.is_symlink()
            for parent in path.parents
            if parent.is_relative_to(paths.generated_dir)
        ):
            raise ValueError("Application generation contains a symlink")
        if path.is_file():
            if path.stat().st_nlink != 1:
                raise ValueError("Application generation contains a linked file")
            files[path.relative_to(paths.generated_dir).as_posix()] = (
                "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            )
    body = {"schema": "nebius-cxcli.application-files/v1", "files": files}
    return {**body, "sha256": digest(body)}


def verify_application_files(paths, manifest: Mapping) -> None:
    expected = manifest.get("render", {}).get("application_files")
    if expected != application_files(paths):
        raise ValueError("Frozen application files changed or are missing; rerender required")


def refresh_application_manifest(config, paths, manifest):
    """Refresh app evidence, retaining protected and infrastructure evidence."""
    from .compatibility_runtime import selected_inventory
    from .frozen_catalog import freeze_catalog

    updated = copy.deepcopy(manifest)
    updated["render"].pop("chart_upgrade", None)
    payload = to_plain_data(config)
    selected = selected_inventory(config)
    roots = [row for row in selected if row["kind"] == "helm" and row["owner"] != "soperator"]
    fresh = freeze_compatibility(config, paths, inventory=roots)
    old = manifest["render"]["compatibility"]
    retained = [
        row for row in old["inventory"] if row["kind"] == "terraform" or row["owner"] == "soperator"
    ]
    retained_ids = {digest(row) for row in retained}
    root_map = {
        (row["component_id"], row["instance_id"]): row
        for row in [*retained, *fresh["inventory"]]
        if "parent_sha256" not in row
    }
    inventory = [root_map[(row["component_id"], row["instance_id"])] for row in selected]
    inventory.extend(row for row in [*retained, *fresh["inventory"]] if "parent_sha256" in row)
    evidence = [row for row in old["receipts"] if row["subject_sha256"] in retained_ids] + fresh[
        "receipts"
    ]
    frozen = {
        **fresh,
        "inventory": inventory,
        "receipts": evidence,
        "report": assess(inventory, receipts=evidence),
    }
    frozen.pop("sha256")
    frozen["sha256"] = digest(frozen)
    bind_application_artifacts(paths, payload, frozen["chart_inputs"])
    updated["runtime_config"] = payload
    updated["render"].update(
        inputs=freeze_catalog(payload),
        compatibility=frozen,
        application_files=application_files(paths),
    )
    return updated


@contextmanager
def application_inputs(config, manifest: Mapping):
    with (
        use_frozen_catalog(manifest.get("render", {}).get("inputs", {})),
        frozen_chart_inputs(
            manifest.get("render", {}).get("compatibility", {}).get("chart_inputs", {})
        ),
    ):
        validate_frozen_compatibility(
            config, manifest.get("render", {}).get("compatibility", {}), matrix=load_matrix()
        )
        yield


def admit_applications(
    config, paths, manifest: Mapping, *, target_refs: Sequence[str], ordinary=False
) -> dict:
    """Full integrity precedes scoped checks; no Terraform validation is fabricated."""
    with application_inputs(config, manifest):
        verify_application_files(paths, manifest)
        require_rendered_application_bindings(
            config, paths, manifest, target_refs, ordinary=ordinary
        )
        frozen = manifest["render"]["compatibility"]
        inventory = [
            copy.deepcopy(row)
            for row in frozen["inventory"]
            if row["kind"] != "terraform"
            and row["instance_id"].split("/", 1)[0] in target_refs
            and (not ordinary or row["owner"] != "soperator")
        ]
        roots = [row for row in inventory if "parent_sha256" not in row]
        # Recapture only the selected application closure. Unselected targets
        # still participate in whole-generation integrity, not execution checks.
        fresh = freeze_compatibility(config, paths, inventory=roots)
        admit_chart_sources(fresh["chart_inputs"])
        identities = {digest(row) for row in inventory}
        evidence = [row for row in frozen["receipts"] if row["subject_sha256"] in identities]
        if fresh["inventory"] != inventory or fresh["receipts"] != evidence:
            raise ValueError("Application artifact or tool inputs changed; rerender required")
        payload = copy.deepcopy(to_plain_data(config))
        for section, key in (("infra", "components"), ("apps", "charts")):
            payload.setdefault(section, {})[key] = [
                row
                for row in payload.get(section, {}).get(key, [])
                if component_instance_id(row) in target_refs
            ]
        from .mk8s_gpu import mk8s_gpu_dependency_issues

        for subject in inventory:
            if subject.get(
                "enabled", True
            ) and "gpu_network_driver_and_nfd_ownership" in subject.get("required_adapters", []):
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
        report = assess(inventory, matrix=load_matrix(), receipts=evidence, admission=True)
        require_admitted(report)
        return {
            **report,
            "execution_scope": "applications",
            "target_refs": list(target_refs),
            "frozen_sha256": frozen["sha256"],
            "application_files_sha256": manifest["render"]["application_files"]["sha256"],
        }


def require_rendered_application_bindings(config, paths, manifest, target_refs, *, ordinary=False):
    from .deployment_observation import target_documents
    from .mysterybox_eso import mysterybox_eso_enabled, require_rendered_mysterybox_bindings
    from .nfs_csi import nfs_instance_id_for_target, require_rendered_nfs_csi_bindings

    payload = to_plain_data(config)
    generation = None
    for ref in target_refs:
        if not (
            nfs_instance_id_for_target(payload, target_ref=ref)
            or mysterybox_eso_enabled(payload, target_ref=ref)
        ):
            continue
        if generation is None:
            generation = DeploymentGeneration.capture(paths, manifest)
        documents = list(target_documents(generation, ref, ordinary_only=ordinary).values())
        require_rendered_nfs_csi_bindings(payload, target_ref=ref, documents=documents)
        require_rendered_mysterybox_bindings(
            payload, target_ref=ref, documents=documents, scope="ordinary" if ordinary else "all"
        )


@contextmanager
def captured_application_execution(config, paths, manifest, *, target_refs, ordinary=False):
    """Apply a private copy of exactly the bytes inspected before any effects."""
    with application_inputs(config, manifest):
        verify_application_files(paths, manifest)
        generation = DeploymentGeneration.capture(paths, manifest)
        stage = staged_generated_paths(paths)
        stage.generated_dir.rmdir()
        try:
            captured_manifest = generation.materialize(stage)
            report = admit_applications(
                config, stage, captured_manifest, target_refs=target_refs, ordinary=ordinary
            )
            yield stage, captured_manifest, report
        finally:
            reset_generated_bundle(stage)


def bind_application_artifacts(paths, config, inputs):
    from .compatibility_artifacts import bind_flux_artifacts
    from .deploy_targets import enabled_cluster_target_refs, flux_target_dir

    if any(
        row.get("id") == "soperator" or "soperator_registration" in row
        for row in to_plain_data(config).get("apps", {}).get("charts", [])
    ):
        for ref in enabled_cluster_target_refs(config):
            bind_flux_artifacts(
                replace(paths, flux_dir=flux_target_dir(paths, ref) / "ordinary"), inputs
            )
    else:
        bind_flux_artifacts(paths, inputs)
