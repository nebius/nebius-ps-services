"""Bridge one sealed install repair across the shared application checkpoint."""

from __future__ import annotations

import base64
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from .deployment_applications import target_bundle_digest
from .deployment_state import DeploymentGeneration, digest
from .soperator_install_docker_storage_repair import (
    docker_storage_candidate,
    prepare_install_docker_storage_repair,
)
from .soperator_install_render_repair import (
    DOCKER_STORAGE_REPAIR_REASON,
    REPAIR_REASON,
    _file_hashes,
    _files,
    prepare_install_dashboard_repair,
)
from .soperator_receipt_io import read_owner_only_json


def unbound_application_inputs_match(
    frozen: DeploymentGeneration, replay: DeploymentGeneration, target_ref: str
) -> bool:
    """Compare unbound inputs without treating Helm YAML key order as drift."""
    import yaml

    from .deployment_observation import target_documents

    class UniqueValuesLoader(yaml.SafeLoader):
        pass

    def mapping(loader: Any, node: Any) -> dict[str, Any]:
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if not isinstance(key, str) or key in result:
                raise ValueError("Ambiguous frozen Helm values mapping")
            result[key] = loader.construct_object(value_node)
        return result

    UniqueValuesLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)

    def inputs(generation: DeploymentGeneration) -> dict[str, Any]:
        documents = target_documents(generation, target_ref)
        for document in documents.values():
            if (
                document.get("apiVersion") == "v1"
                and document.get("kind") == "ConfigMap"
                and document.get("metadata", {}).get("namespace") == "flux-system"
                and document.get("metadata", {}).get("name") == "terraform-fluxcd-values"
                and "values.yaml" in document.get("data", {})
            ):
                document["data"]["values.yaml"] = yaml.load(
                    document["data"]["values.yaml"], Loader=UniqueValuesLoader
                )
        return documents

    try:
        # Typed canonical JSON keeps booleans, numbers, strings and list order
        # distinct. This comparison never changes files or a bound bundle digest.
        return digest(inputs(frozen)) == digest(inputs(replay))
    except (yaml.YAMLError, TypeError, ValueError, RecursionError):
        return False


@dataclass(frozen=True)
class InstallInputTransition:
    previous_bundle: str
    desired_bundle: str
    previous_files: Mapping[str, str]
    replacement_files: Mapping[str, str]
    reason: str = DOCKER_STORAGE_REPAIR_REASON
    replacement_generation: DeploymentGeneration | None = None


def install_input_transition(executor: Any, **kwargs: Any) -> InstallInputTransition | None:
    from .deployment_dashboard_repair import dashboard_input_transition

    dashboard = dashboard_input_transition(executor, **kwargs)
    return dashboard if dashboard is not None else storage_input_transition(**kwargs)


def storage_input_transition(
    *,
    current: DeploymentGeneration,
    desired: DeploymentGeneration,
    target_ref: str,
    entry: Mapping[str, Any],
) -> InstallInputTransition | None:
    """Admit no other rendered drift; the journal authenticates the prior documents."""
    desired_bundle = target_bundle_digest(desired, target_ref)
    if not entry or entry["desiredBundle"] == desired_bundle:
        return None
    if entry.get("status") != "executing" or entry.get("inputRepair") or entry.get("evidence"):
        raise RuntimeError("Application render repair requires an unfinished initial bundle")
    rows = [r for r in desired.manifest["deploy"]["targets"] if r["target_ref"] == target_ref]
    root = rows[0]["flux_dir"].rstrip("/") + "/"

    def files(generation: DeploymentGeneration) -> dict[str, bytes]:
        return {
            name.removeprefix(root): base64.b64decode(data, validate=True)
            for name, data in generation.files.items()
            if name.startswith(root)
        }

    before, after = files(current), files(desired)
    # A runner may have committed the sealed file transaction before publishing
    # the application journal. Its immutable inverse must still match the journal.
    if before == after:
        before = docker_storage_candidate(before, inverse=True)
    reconstructed = replace(
        current,
        files={
            **current.files,
            **{root + name: base64.b64encode(data).decode() for name, data in before.items()},
        },
    )
    if (
        target_bundle_digest(reconstructed, target_ref) != entry["desiredBundle"]
        or docker_storage_candidate(before) != after
        or docker_storage_candidate(after, inverse=True) != before
        or before == after
    ):
        raise RuntimeError("Application bundle drift is outside the exact Docker storage repair")
    return InstallInputTransition(
        entry["desiredBundle"], desired_bundle, _file_hashes(before), _file_hashes(after)
    )


def admit_storage_input_transition(
    executor: Any,
    ref: str,
    identity: Mapping[str, str],
    kube_env: Mapping[str, str],
    assert_authority: Any,
) -> None:
    transition = getattr(executor, "_install_input_transitions", {}).get(ref)
    if transition is None:
        return
    cli = executor.cli
    paths = cli._paths_for_target_flux_dir(executor.paths, executor._selected_target(ref))
    hashes = _file_hashes(_files(paths.flux_dir))
    if hashes not in (transition.previous_files, transition.replacement_files):
        raise RuntimeError("Application input repair files changed before cluster admission")
    context = kube_env[cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV]
    record = cli._read_soperator_slurm_cluster_journal(
        target_ref=ref,
        cluster_id=identity["cluster_id"],
        kube_context=context,
        extra_env=kube_env,
    )
    journal_path = paths.reports_dir / f"soperator-slurm-actions-{ref}.json"
    if record is None or not journal_path.exists():
        raise RuntimeError("Application input repair lost its scheduling predecessor")
    local = read_owner_only_json(journal_path, label="Install scheduling predecessor")
    prepare: Callable[..., Mapping[str, Any] | None] = prepare_install_docker_storage_repair
    extra: dict[str, Any] = {
        "slurm": lambda command: (
            cli._run_soperator_upgrade_login_command(
                "soperator", command, kube_context=context, extra_env=kube_env
            ).stdout
        )
    }
    if transition.reason == REPAIR_REASON:
        prepare = prepare_install_dashboard_repair
        extra = {}
    elif transition.reason != DOCKER_STORAGE_REPAIR_REASON:
        from .deployment_bundle_files import target_files
        from .soperator_install_observability_repair import prepare_install_observability_repair

        desired = transition.replacement_generation
        assert desired is not None
        prepare = prepare_install_observability_repair
        extra = {
            **extra,
            "candidate": target_files(desired, ref),
            "config": desired.manifest["runtime_config"],
            "render_inputs": desired.manifest.get("render", {}).get("application_inputs", {}),
            "identity": identity,
            "chart_inputs": desired.manifest.get("render", {})
            .get("compatibility", {})
            .get("chart_inputs", {}),
        }
    repair = prepare(
        paths=paths,
        target_ref=ref,
        scheduling_journal=record[0],
        local_scheduling_journal=local,
        env={**os.environ, **kube_env},
        kube_context=context,
        assert_authority=assert_authority,
        **extra,
    )
    if (
        repair is None
        or repair["schema"] != transition.reason
        or repair["previousFiles"] != transition.previous_files
        or repair["replacementFiles"] != transition.replacement_files
        or repair["predecessorReceipt"]["operation"]["spec"]["nebius_cluster_id"]
        != identity["cluster_id"]
        or repair["predecessorReceipt"]["operation"]["spec"]["kubernetes_uid"]
        != identity["kubernetes_uid"]
    ):
        raise RuntimeError("Application input repair differs from the cluster-sealed delta")
    assert_authority()
    executor._application_journal().repair_inputs(
        ref,
        identity=identity,
        previous=transition.previous_bundle,
        desired=transition.desired_bundle,
        admission_sha256=digest(repair),
        reason=transition.reason,
    )
    del executor._install_input_transitions[ref]
