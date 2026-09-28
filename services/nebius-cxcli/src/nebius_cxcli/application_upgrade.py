"""Prepare, admit and atomically publish one ordinary Helm chart upgrade."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from dataclasses import dataclass

from .application_compatibility import (
    admit_applications,
    application_files,
    application_inputs,
    bind_application_artifacts,
    refresh_application_manifest,
    verify_application_files,
)
from .application_execution import (
    observe_application_targets,
    require_same_upgrade_target,
    validate_observed_applications,
)
from .compatibility_artifacts import frozen_chart_inputs
from .compatibility_execution import freeze_compatibility
from .compatibility_matrix import digest
from .compatibility_runtime import selected_inventory
from .compatibility_transitions import assess_chart_upgrade
from .deployment_recovery import deployment_preview
from .deployment_state import DeploymentGeneration, admitted_application_generation
from .frozen_catalog import use_frozen_catalog
from .generated_manifest import manifest_path_for_generated_dir, runtime_config_from_manifest
from .ordinary_apps import (
    BASELINE_FILENAME,
    _ordinary_files,
    _protected_files,
    prepare_ordinary_apps,
)
from .project_bundle_transaction import ProjectBundleTransaction
from .render import reset_generated_bundle, staged_generated_paths
from .runtime_config import to_plain_data


@dataclass
class ChartUpgradeCandidate:
    config: object
    paths: object
    manifest: dict
    writes: dict
    removals: set
    expected_preimages: dict
    admission: dict
    transition: dict
    observations: dict


def _encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _intent(manifest):
    intent = manifest.get("render", {}).get("chart_upgrade")
    if intent is None:
        return None
    if intent.get("schema") != "nebius-cxcli.chart-upgrade/v1" or intent.get("sha256") != digest(
        {key: value for key, value in intent.items() if key != "sha256"}
    ):
        raise ValueError("Chart upgrade evidence is invalid; restore the admitted generation")
    if (
        intent["target_compatibility_sha256"] != manifest["render"]["compatibility"]["sha256"]
        or intent["application_files_sha256"] != manifest["render"]["application_files"]["sha256"]
    ):
        raise ValueError("Chart upgrade evidence does not bind this generation")
    return intent


@contextmanager
def prepare_chart_upgrade(cli, source_payload, source_preimage, config, paths, manifest, plan):
    """No project writes: even the transaction's preimages precede artifact resolution."""
    transaction = ProjectBundleTransaction(paths.project_dir)
    source_path = paths.config_path
    manifest_path = manifest_path_for_generated_dir(paths.generated_dir)
    originals = {
        source_path,
        manifest_path,
        *paths.flux_dir.rglob("*"),
        *_protected_files(paths),
        *_ordinary_files(paths, config),
        paths.reports_dir / BASELINE_FILENAME,
    }
    originals = {path for path in originals if path.is_file()}
    preimages = transaction.snapshot_preimages(originals, read_only=True)
    if preimages[source_path].content != source_preimage:
        raise ValueError("Configuration changed before upgrade preparation")
    if json.loads(preimages[manifest_path].content) != manifest:
        raise ValueError("Generated manifest changed before upgrade preparation; retry the command")
    if manifest["render"].get("source_config_sha256") != digest(source_payload):
        raise ValueError(
            "Configuration differs from the rendered source; render the configuration, then retry the upgrade"
        )
    with deployment_preview(True), application_inputs(config, manifest):
        verify_application_files(paths, manifest)
        source_generation = DeploymentGeneration.capture(paths, manifest)
    selected = cli._resolve_selected_deploy_targets(
        manifest, requested_target_ref=plan.target.target_ref, all_targets=False
    )
    observed_manifest = copy.deepcopy(manifest)
    observations = observe_application_targets(
        cli, config, paths, observed_manifest, selected, plan=plan
    )
    live_version = observations[plan.target.target_ref]["release"]["chart_version"].removeprefix(
        "v"
    )
    existing = _intent(manifest)
    retry = bool(
        existing
        and existing["selector"] == plan.target.selector
        and existing["target_version"] == plan.target_version
    )
    if retry:
        require_same_upgrade_target(existing["source_observation"], observations)
    allowed = {plan.current_version.removeprefix("v")}
    if retry:
        allowed.add(existing["source_version"].removeprefix("v"))
    if live_version not in allowed:
        raise ValueError("Live chart version differs from the frozen upgrade source")
    stage = staged_generated_paths(paths)
    stage.generated_dir.rmdir()
    try:
        staged_manifest = source_generation.materialize(stage)
        if retry or not plan.mutates:
            with deployment_preview(True):
                admission = admit_applications(
                    config,
                    stage,
                    staged_manifest,
                    target_refs=[plan.target.target_ref],
                    ordinary=cli._payload_has_soperator_lifecycle(config),
                )
            validate_observed_applications(
                staged_manifest, observations, ordinary=cli._payload_has_soperator_lifecycle(config)
            )
            yield ChartUpgradeCandidate(
                config,
                stage,
                staged_manifest,
                {},
                set(),
                {},
                admission,
                existing["transition"] if retry else {},
                observations,
            )
            return
        candidate_payload = copy.deepcopy(source_payload)
        cli._update_source_helm_chart_version(
            candidate_payload, target=plan.target, target_version=plan.target_version
        )
        candidate_text = cli.render_updated_source_payload(candidate_payload).encode()
        candidate = copy.deepcopy(to_plain_data(config))
        cli._update_source_helm_chart_version(
            candidate, target=plan.target, target_version=plan.target_version
        )
        ordinary = cli._payload_has_soperator_lifecycle(config)
        with deployment_preview(True), use_frozen_catalog(manifest["render"]["inputs"]):
            cli.materialize_mk8s_gpu_app_values(candidate)
            cli.materialize_observability_app_values(candidate)
            from .deployment_resolution import resolve_application_outputs

            outputs = resolve_application_outputs(
                cli, candidate, paths, include_soperator_handoffs=False
            )
            # Resolve only the changed artifact; unchanged references must replay
            # their original frozen bytes, even if the registry tag has moved.
            root = next(
                row
                for row in selected_inventory(candidate)
                if row["component_id"] == plan.target.chart_id
                and row["instance_id"] == plan.target.target_ref
            )
            changed = freeze_compatibility(candidate, stage, inventory=[root])
            inputs = {
                **manifest["render"]["compatibility"]["chart_inputs"],
                **changed["chart_inputs"],
            }
            with frozen_chart_inputs(inputs):
                if ordinary:
                    writes, removals, expected, staged_manifest = prepare_ordinary_apps(
                        candidate,
                        paths,
                        source_preimage=source_preimage,
                        source_content=candidate_text,
                        component_output_values=outputs,
                    )
                    # Project locators stay canonical in the published manifest.
                    for path, content in writes.items():
                        if path.is_relative_to(paths.generated_dir):
                            target = stage.generated_dir / path.relative_to(paths.generated_dir)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(content)
                    for path in removals:
                        (stage.generated_dir / path.relative_to(paths.generated_dir)).unlink(
                            missing_ok=True
                        )
                    candidate = staged_manifest["runtime_config"]
                else:
                    for path in stage.flux_dir.rglob("*"):
                        if path.is_file():
                            path.unlink()
                    cli.render_flux(candidate, stage, component_output_values=outputs)
                    staged_manifest = refresh_application_manifest(
                        candidate, stage, staged_manifest
                    )
                    writes, removals, expected = {}, set(), {}
                bind_application_artifacts(
                    stage, candidate, staged_manifest["render"]["compatibility"]["chart_inputs"]
                )
                staged_manifest["render"]["application_files"] = application_files(stage)
                staged_manifest["render"]["source_config_sha256"] = digest(candidate_payload)
                admission = admit_applications(
                    candidate,
                    stage,
                    staged_manifest,
                    target_refs=[plan.target.target_ref],
                    ordinary=ordinary,
                )
                admission["observed_compatibility"] = validate_observed_applications(
                    staged_manifest, observations, ordinary=ordinary
                )
                transition = assess_chart_upgrade(
                    manifest, staged_manifest, target_ref=plan.target.target_ref
                )
        evidence = {
            "schema": "nebius-cxcli.chart-upgrade/v1",
            "selector": plan.target.selector,
            "source_version": plan.current_version,
            "target_version": plan.target_version,
            "source_generation": source_generation.identity,
            "source_observation": observations,
            "target_compatibility_sha256": staged_manifest["render"]["compatibility"]["sha256"],
            "application_files_sha256": staged_manifest["render"]["application_files"]["sha256"],
            "transition": transition,
        }
        staged_manifest["render"]["chart_upgrade"] = {**evidence, "sha256": digest(evidence)}
        # Keep canonical locators when publishing; stage locators are execution-only.
        staged_manifest["paths"] = manifest["paths"]
        staged_manifest["deploy"]["targets"] = manifest["deploy"]["targets"]
        for path in stage.flux_dir.rglob("*"):
            if path.is_file():
                writes[paths.flux_dir / path.relative_to(stage.flux_dir)] = path.read_bytes()
        writes[source_path] = candidate_text
        writes[manifest_path] = _encode(staged_manifest)
        removals |= {
            path for path in originals if path.is_relative_to(paths.flux_dir) and path not in writes
        }
        expected.update(
            {
                path: preimages[path].sha256 if path in preimages else "absent"
                for path in {*writes, *removals}
            }
        )
        # Recheck the initial file inventory too, detecting concurrent new files.
        if {path for path in paths.flux_dir.rglob("*") if path.is_file()} != {
            path for path in originals if path.is_relative_to(paths.flux_dir)
        }:
            raise ValueError("Application file inventory changed during preparation")
        yield ChartUpgradeCandidate(
            runtime_config_from_manifest(staged_manifest),
            stage,
            staged_manifest,
            writes,
            removals,
            expected,
            admission,
            transition,
            observations,
        )
    finally:
        reset_generated_bundle(stage)


def run_chart_upgrade(
    cli, source_payload, source_preimage, config, paths, manifest, plan, *, dry_run
):
    with prepare_chart_upgrade(
        cli, source_payload, source_preimage, config, paths, manifest, plan
    ) as candidate:
        if dry_run:
            cli.console.print("Chart upgrade assessment complete; no changes published or applied.")
            return
        if candidate.writes:
            ProjectBundleTransaction(paths.project_dir).commit(
                candidate.writes,
                removals=candidate.removals,
                expected_preimages=candidate.expected_preimages,
            )
        generation = DeploymentGeneration.capture(candidate.paths, candidate.manifest)
        with admitted_application_generation(
            paths, generation, observations=candidate.observations
        ):
            if cli._payload_has_soperator_lifecycle(candidate.config):
                cli.deploy_command(paths.config_path, target_ref=plan.target.target_ref)
            else:
                cli.flux_apply_command(
                    paths.generated_dir, target_ref=plan.target.target_ref, all_targets=False
                )
        cli._verify_helm_chart_upgrade_ready(
            candidate.config, paths, candidate.manifest, plan, observations=candidate.observations
        )
        cli.console.print(
            f"[green]Helm chart upgrade completed[/green]: {plan.target.selector} -> {plan.target_version}"
        )
