"""Ordinary app generation on clusters whose infrastructure has a lifecycle owner.

The accepted baseline is written only by successful lifecycle operations.
Ordinary commands compare it and publish only app resources and their manifest.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from . import kubernetes_process
from .app_mutation import app_mutation_scope
from .component_instances import component_type_id
from .deploy_targets import enabled_cluster_target_refs, flux_target_dir
from .flux_render import render_flux
from .generated_manifest import (
    load_generated_manifest,
    manifest_path_for_generated_dir,
    runtime_config_from_manifest,
)
from .grafana_runtime import (
    GRAFANA_TARGET_CLUSTER_ID_ENV,
    GRAFANA_TARGET_KUBE_CONTEXT_ENV,
    grafana_release_specs,
    write_grafana_status,
)
from .observability import materialize_observability_app_values, observability_validation_specs
from .observability_validation import run_observability_validations
from .paths import ProjectPaths
from .project_bundle_transaction import ProjectBundlePreimageConflict, ProjectBundleTransaction
from .render import reset_generated_bundle, staged_generated_paths
from .runtime_config import to_plain_data
from .soperator_operation_lock import SoperatorOperationLease, SoperatorOperationLocalLock
from .soperator_status import read_soperator_operation_status

BASELINE_FILENAME = "ordinary-apps-baseline.json"


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def protected_config_digest(config: Any) -> str:
    payload = copy.deepcopy(to_plain_data(config))
    if not isinstance(payload, dict):
        raise ValueError("App scope requires a configuration mapping")
    apps = payload.get("apps", {})
    if isinstance(apps, dict):
        apps["charts"] = [
            row
            for row in apps.get("charts", [])
            if isinstance(row, Mapping)
            and (component_type_id(row) == "soperator" or "soperator_registration" in row)
        ]
    native_targets = {
        row.get("target_ref") or row.get("instance_id")
        for row in apps.get("charts", [])
        if row.get("id") == "soperator"
    }
    for target in payload.get("deploy", {}).get("targets", []):
        if isinstance(target, dict):
            # Native routing changes its protected child HelmReleases. Additional
            # app telemetry remains ordinary-owned.
            routing = target.get("observability", {}).get("routing")
            target.pop("observability", None)
            if routing and target.get("instance_id") in native_targets:
                target["observability"] = {"routing": routing}
    return _digest(_json_bytes(payload))


def ordinary_config(config: Any) -> dict[str, Any]:
    payload = copy.deepcopy(to_plain_data(config))
    payload.setdefault("apps", {})["charts"] = [
        row
        for row in payload.get("apps", {}).get("charts", [])
        if component_type_id(row) != "soperator" and "soperator_registration" not in row
    ]
    return payload


def publish_ordinary_app_config(
    paths: ProjectPaths,
    candidate: Mapping[str, Any],
    *,
    expected_bytes: bytes | None = None,
    observability_target_refs: frozenset[str] = frozenset(),
) -> bool:
    """Publish only app configuration, preserving authored protected subtrees."""
    with SoperatorOperationLocalLock(paths.project_dir / ".nebius-cxcli" / "config.lock"):
        current_bytes = paths.config_path.read_bytes()
        if expected_bytes is not None and expected_bytes != current_bytes:
            raise RuntimeError("Config changed during ordinary app selection")
        source = yaml.safe_load(current_bytes)
        current_infra = {
            (component_type_id(row), row.get("instance_id"), row.get("enabled"))
            for row in source.get("infra", {}).get("components", [])
        }
        proposed_infra = {
            (component_type_id(row), row.get("instance_id"), row.get("enabled"))
            for row in candidate.get("infra", {}).get("components", [])
        }
        if current_infra != proposed_infra:
            raise RuntimeError(
                "Ordinary app selection cannot change protected infrastructure components"
            )
        updated = copy.deepcopy(source)
        updated.setdefault("apps", {})["charts"] = [
            row
            for row in source.get("apps", {}).get("charts", [])
            if component_type_id(row) == "soperator" or "soperator_registration" in row
        ] + ordinary_config(candidate).get("apps", {}).get("charts", [])
        settings = {
            row.get("instance_id"): row.get("observability")
            for row in candidate.get("deploy", {}).get("targets", [])
        }
        for row in updated.get("deploy", {}).get("targets", []):
            if settings.get(row.get("instance_id")) is not None:
                row["observability"] = copy.deepcopy(settings[row["instance_id"]])
        from .observability_routing import app_row, resolve_settings

        for target in observability_target_refs:
            if app_row(updated, target, "grafana") is None:
                raise ValueError("Observability config publication requires selected Grafana")
            resolve_settings(updated, target)

        def publication_digest(payload: Mapping[str, Any]) -> str:
            comparable = copy.deepcopy(dict(payload))
            for row in comparable.get("deploy", {}).get("targets", []):
                if row.get("instance_id") in observability_target_refs:
                    row.get("observability", {}).pop("routing", None)
            return protected_config_digest(comparable)

        # Only the explicitly selected routing intent is editable here. Native
        # charts/infra come from source, and the saved protected digest remains
        # changed so ordinary rendering/application cannot bypass native admission.
        if publication_digest(updated) != publication_digest(source):
            raise RuntimeError("Ordinary app selection changed protected configuration")
        if updated == source:
            return False
        ProjectBundleTransaction(paths.project_dir).commit(
            {paths.config_path: yaml.safe_dump(updated, sort_keys=False)},
            expected_preimages={paths.config_path: _digest(current_bytes)},
        )
        return True


def _protected_files(paths: ProjectPaths) -> tuple[Path, ...]:
    infra = (
        path
        for path in paths.infra_dir.rglob("*")
        if path.is_file()
        and ".terraform" not in path.relative_to(paths.infra_dir).parts
        and (path.name.endswith((".tf", ".tfvars.json")) or path.name == ".terraform.lock.hcl")
    )
    flux = (
        path
        for path in paths.flux_dir.rglob("*")
        if path.is_file() and "ordinary" not in path.relative_to(paths.flux_dir).parts
    )
    return tuple(sorted((*infra, *flux)))


def _ordinary_files(paths: ProjectPaths, config: Any) -> tuple[Path, ...]:
    roots = [
        flux_target_dir(paths, ref) / "ordinary" for ref in enabled_cluster_target_refs(config)
    ]
    roots.append(paths.generated_dir / "grafana_dashboards")
    files = []
    for root in roots:
        if root.resolve() != root.absolute() or root.is_symlink():
            raise RuntimeError("Ordinary app bundle has an unsafe path")
        for path in root.rglob("*"):
            if path.is_symlink() or (path.is_file() and path.stat().st_nlink != 1):
                raise RuntimeError("Ordinary app bundle has unsafe file ownership")
            if path.is_file():
                files.append(path)
    return tuple(sorted(files))


def _hash_files(paths: ProjectPaths) -> dict[str, str]:
    result = {}
    for path in _protected_files(paths):
        if path.is_symlink() or path.stat().st_nlink != 1:
            raise RuntimeError("Protected generated artifact has unsafe ownership")
        result[path.relative_to(paths.generated_dir).as_posix()] = _digest(path.read_bytes())
    return result


def accept_ordinary_app_baseline(
    paths: ProjectPaths,
    *,
    identities: Mapping[str, Mapping[str, str]] | None = None,
    selected_target_refs: Sequence[str] | None = None,
    deployment_generation: str = "",
    expected_generation: Any = None,
) -> bool:
    """Record successful lifecycle postconditions, never an ordinary candidate."""
    manifest = load_generated_manifest(paths.generated_dir)
    baseline_path = paths.reports_dir / BASELINE_FILENAME
    prior = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    bindings = dict(prior.get("identities", {}))
    bindings.update({key: dict(value) for key, value in (identities or {}).items()})
    target_refs = set(enabled_cluster_target_refs(manifest["runtime_config"]))
    bindings = {key: value for key, value in bindings.items() if key in target_refs}
    transaction = ProjectBundleTransaction(paths.project_dir)
    manifest_path = manifest_path_for_generated_dir(paths.generated_dir)
    protected_paths = tuple(_protected_files(paths))
    generation_paths = (
        tuple(paths.generated_dir / name for name in expected_generation.files)
        if expected_generation
        else ()
    )
    targets = tuple(
        dict.fromkeys(
            (paths.config_path, manifest_path, baseline_path, *protected_paths, *generation_paths)
        )
    )
    preimages = transaction.snapshot_preimages(targets)
    manifest_bytes = preimages[manifest_path].content
    source_bytes = preimages[paths.config_path].content
    if expected_generation is not None:
        from base64 import b64decode

        from .compatibility_matrix import digest as source_digest
        from .deployment_state import DeploymentGeneration

        if deployment_generation != expected_generation.identity:
            raise RuntimeError("App baseline was given a different accepted generation")
        expected_source = expected_generation.manifest.get("render", {}).get("source_config_sha256")
        if (
            not expected_source
            or source_bytes is None
            or source_digest(yaml.safe_load(source_bytes)) != expected_source
        ):
            return False
        if manifest_bytes is None:
            return False
        current = DeploymentGeneration.capture(paths, json.loads(manifest_bytes))
        if current.identity != expected_generation.identity:
            return False
        for name, encoded in expected_generation.files.items():
            if preimages[paths.generated_dir / name].content != b64decode(encoded, validate=True):
                return False
    if manifest_bytes is None or source_bytes is None:
        raise RuntimeError("Lifecycle acceptance lost its generated manifest or source")
    accepted_manifest = json.loads(manifest_bytes)
    if accepted_manifest != manifest:
        raise RuntimeError("Generated manifest changed during lifecycle acceptance")
    baseline = {
        "schema": "nebius-cxcli-ordinary-apps/v1",
        "deployment_generation": deployment_generation,
        "source_sha256": protected_config_digest(yaml.safe_load(source_bytes)),
        "runtime_sha256": protected_config_digest(accepted_manifest["runtime_config"]),
        "protected_files": {
            path.relative_to(paths.generated_dir).as_posix(): item.sha256
            for path, item in preimages.items()
            if path in protected_paths
        },
        "identities": bindings,
        "ordinary_files": _accepted_ordinary_files(
            paths,
            manifest["runtime_config"],
            prior.get("ordinary_files", {}),
            selected_target_refs,
        ),
    }
    writes = {path: item.content for path, item in preimages.items() if item.content is not None}
    writes[baseline_path] = _json_bytes(baseline)
    try:
        transaction.commit(
            writes, expected_preimages={p: item.sha256 for p, item in preimages.items()}
        )
    except ProjectBundlePreimageConflict:
        # The deployment already succeeded. Preserve the user's newer source and
        # leave app-only admission closed until a baseline can be accepted again.
        return False
    return True


def _accepted_ordinary_files(
    paths: ProjectPaths,
    config: Any,
    prior: Mapping[str, str],
    selected: Sequence[str] | None,
) -> dict[str, str]:
    files = _ordinary_files(paths, config)
    if selected is None:
        return {
            path.relative_to(paths.generated_dir).as_posix(): _digest(path.read_bytes())
            for path in files
        }
    roots = tuple(flux_target_dir(paths, ref) / "ordinary" for ref in selected)

    def owned(path: Path) -> bool:
        return any(path.is_relative_to(root) for root in roots)

    result = {name: value for name, value in prior.items() if not owned(paths.generated_dir / name)}
    result.update(
        {
            path.relative_to(paths.generated_dir).as_posix(): _digest(path.read_bytes())
            for path in files
            if owned(path)
        }
    )
    return result


def preserve_ordinary_app_generation(paths: ProjectPaths, staged: ProjectPaths) -> None:
    """Carry the last generated app contract through protected lifecycle renders."""
    baseline_path = paths.reports_dir / BASELINE_FILENAME
    if not baseline_path.is_file():
        return
    current = load_generated_manifest(paths.generated_dir)
    candidate_path = manifest_path_for_generated_dir(staged.generated_dir)
    candidate = load_generated_manifest(staged.generated_dir)
    baseline = json.loads(baseline_path.read_text())
    expected = current.get("render", {}).get("ordinary_files", baseline.get("ordinary_files", {}))
    actual = {
        path.relative_to(paths.generated_dir).as_posix(): _digest(path.read_bytes())
        for path in _ordinary_files(paths, current["runtime_config"])
    }
    if actual != expected:
        raise RuntimeError(
            "Ordinary app artifacts changed before lifecycle render; render them first"
        )
    for target_ref in enabled_cluster_target_refs(candidate["runtime_config"]):
        destination = flux_target_dir(staged, target_ref) / "ordinary"
        if destination.exists():
            shutil.rmtree(destination)
    dashboards = staged.generated_dir / "grafana_dashboards"
    if dashboards.exists():
        shutil.rmtree(dashboards)
    for relative in actual:
        destination = staged.generated_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((paths.generated_dir / relative).read_bytes())
        if _digest(destination.read_bytes()) != actual[relative]:
            raise RuntimeError("Ordinary apps changed during lifecycle staging")
    candidate["runtime_config"]["apps"]["charts"] = [
        row
        for row in candidate["runtime_config"].get("apps", {}).get("charts", [])
        if component_type_id(row) == "soperator" or "soperator_registration" in row
    ] + ordinary_config(current["runtime_config"]).get("apps", {}).get("charts", [])
    settings = {
        row.get("instance_id"): row.get("observability")
        for row in current["runtime_config"].get("deploy", {}).get("targets", [])
    }
    for row in candidate["runtime_config"].get("deploy", {}).get("targets", []):
        if settings.get(row.get("instance_id")) is not None:
            row["observability"] = copy.deepcopy(settings[row["instance_id"]])
    for key in ("app_scope", "ordinary_validations", "ordinary_files"):
        if key in current.get("render", {}):
            candidate["render"][key] = copy.deepcopy(current["render"][key])
    from .application_compatibility import refresh_application_manifest
    from .compatibility_artifacts import frozen_chart_inputs
    from .frozen_catalog import use_frozen_catalog

    with (
        use_frozen_catalog(candidate["render"]["inputs"]),
        frozen_chart_inputs(current["render"]["compatibility"].get("chart_inputs", {})),
    ):
        candidate = refresh_application_manifest(candidate["runtime_config"], staged, candidate)
    candidate_path.write_bytes(_json_bytes(candidate))


def validate_ordinary_app_scope(
    paths: ProjectPaths,
    *,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    baseline_path = paths.reports_dir / BASELINE_FILENAME
    if not baseline_path.is_file() or baseline_path.is_symlink():
        raise RuntimeError(
            "Ordinary app changes require a successfully completed Soperator install, "
            "onboard, or upgrade with an accepted generated baseline."
        )
    baseline = json.loads(baseline_path.read_text())
    if baseline.get("schema") != "nebius-cxcli-ordinary-apps/v1":
        raise RuntimeError("Unsupported ordinary app baseline")
    manifest = manifest or load_generated_manifest(paths.generated_dir)
    for target_ref in enabled_cluster_target_refs(manifest["runtime_config"]):
        if read_soperator_operation_status(paths=paths, target_ref=target_ref) is not None:
            raise RuntimeError(
                "A Soperator lifecycle operation requires recovery before ordinary app changes"
            )
    install_path = paths.reports_dir / "soperator-install-plan.json"
    if install_path.exists() and json.loads(install_path.read_text()).get("status") != "complete":
        raise RuntimeError("Complete the pending Soperator install before ordinary app changes")
    source = yaml.safe_load(paths.config_path.read_text())
    if (
        protected_config_digest(source) != baseline.get("source_sha256")
        or protected_config_digest(manifest["runtime_config"]) != baseline.get("runtime_sha256")
        or _hash_files(paths) != baseline.get("protected_files")
    ):
        raise RuntimeError(
            "Protected Soperator configuration or generated infrastructure changed. "
            "Use the dedicated Soperator lifecycle before ordinary app operations."
        )
    return baseline


def resource_documents(flux_dir: Path) -> list[dict[str, Any]]:
    """Read only resources named by this scope's kustomization, with no escapes."""
    path = flux_dir / "kustomization.yaml"
    if not path.is_file():
        return []
    result: list[dict[str, Any]] = []
    resources = list(yaml.safe_load(path.read_text()).get("resources", []))
    resources.extend(
        item.name
        for item in sorted(flux_dir.glob("post-flux-*.yaml"))
        if item.name not in resources
    )
    for resource in resources:
        resource_path = flux_dir / resource
        if resource_path.resolve().parent != flux_dir.resolve() or resource_path.is_symlink():
            raise RuntimeError("App bundle resource escapes its target scope")
        result.extend(
            doc for doc in yaml.safe_load_all(resource_path.read_text()) if isinstance(doc, dict)
        )
    return result


def resource_identity(doc: Mapping[str, Any]) -> tuple[str, str, str, str]:
    metadata = doc.get("metadata", {})
    api_version = str(doc.get("apiVersion", ""))
    return (
        api_version.split("/")[0] if "/" in api_version else "",
        str(doc.get("kind", "")),
        str(metadata.get("namespace", "")),
        str(metadata.get("name", "")),
    )


def _external_secret_target(doc: Mapping[str, Any]) -> tuple[str, str] | None:
    if doc.get("kind") == "Secret":
        metadata = doc.get("metadata", {})
        return str(metadata.get("namespace", "")), str(metadata.get("name", ""))
    if doc.get("kind") != "ExternalSecret":
        return None
    metadata = doc.get("metadata", {})
    return (
        str(metadata.get("namespace", "")),
        str(doc.get("spec", {}).get("target", {}).get("name") or metadata.get("name", "")),
    )


def is_shared_protected_resource(doc: Mapping[str, Any], protected: Mapping[str, Any]) -> bool:
    """Identify an ordinary prerequisite that its protected owner supplies."""
    if resource_identity(doc) != resource_identity(protected):
        return False
    if doc.get("kind") == "Namespace":
        metadata = doc.get("metadata", {})
        return (
            doc.get("apiVersion") == protected.get("apiVersion")
            and set(doc) <= {"apiVersion", "kind", "metadata"}
            and set(metadata) <= {"name", "labels", "annotations"}
            and not metadata.get("labels")
            and set(metadata.get("annotations", {})) <= {"cxcli.nebius.com/app-owner"}
        )
    return doc.get("kind") in {"HelmRepository", "GitRepository", "OCIRepository"} and doc.get(
        "spec"
    ) == protected.get("spec")


def omit_shared_protected_resources(ordinary_dir: Path, protected_dir: Path) -> None:
    """Reference shared namespaces and identical sources without reapplying them."""
    protected = {resource_identity(doc): doc for doc in resource_documents(protected_dir)}
    kustomization_path = ordinary_dir / "kustomization.yaml"
    kustomization = yaml.safe_load(kustomization_path.read_text())
    removed = set()
    for path in ordinary_dir.glob("*.yaml"):
        if path == kustomization_path:
            continue
        docs = []
        for doc in yaml.safe_load_all(path.read_text()):
            if not isinstance(doc, dict):
                continue
            existing = protected.get(resource_identity(doc))
            if existing is not None and is_shared_protected_resource(doc, existing):
                continue
            docs.append(doc)
        if docs:
            path.write_text(yaml.safe_dump_all(docs, sort_keys=False))
        else:
            path.unlink()
            removed.add(path.name)
    kustomization["resources"] = [
        item for item in kustomization.get("resources", []) if Path(item).name not in removed
    ]
    kustomization_path.write_text(yaml.safe_dump(kustomization, sort_keys=False))


def validate_resource_ownership(ordinary_dir: Path, protected_dir: Path) -> None:
    protected = {resource_identity(doc): doc for doc in resource_documents(protected_dir)}
    seen: set[tuple[str, str, str, str]] = set()
    helm_releases: set[tuple[str, str]] = set()
    secret_targets = {
        target for doc in protected.values() if (target := _external_secret_target(doc)) is not None
    }
    for doc in resource_documents(ordinary_dir):
        identity = resource_identity(doc)
        if identity in protected or identity in seen:
            raise RuntimeError(f"Ordinary app resource collides with an existing owner: {identity}")
        seen.add(identity)
        secret_target = _external_secret_target(doc)
        if secret_target is not None:
            if secret_target in secret_targets:
                raise RuntimeError(
                    "Ordinary ExternalSecret collides with an existing target Secret owner"
                )
            secret_targets.add(secret_target)
        if doc.get("kind") == "HelmRelease":
            spec = doc.get("spec", {})
            metadata = doc.get("metadata", {})
            helm_identity = (
                str(spec.get("targetNamespace") or metadata.get("namespace", "")),
                str(spec.get("releaseName") or metadata.get("name", "")),
            )
            if helm_identity in helm_releases:
                raise RuntimeError(f"Ordinary apps repeat a Helm release identity: {helm_identity}")
            helm_releases.add(helm_identity)
            for protected_doc in protected.values():
                if protected_doc.get("kind") != "HelmRelease":
                    continue
                protected_spec = protected_doc.get("spec", {})
                protected_meta = protected_doc.get("metadata", {})
                if helm_identity == (
                    str(
                        protected_spec.get("targetNamespace") or protected_meta.get("namespace", "")
                    ),
                    str(protected_spec.get("releaseName") or protected_meta.get("name", "")),
                ):
                    raise RuntimeError("Ordinary app collides with a protected Helm release")


def render_ordinary_apps(
    config: Any, paths: ProjectPaths, *, source_preimage: bytes | None = None
) -> list[Path]:
    """Prepare and atomically publish ordinary apps with fresh compatibility evidence."""
    with SoperatorOperationLocalLock(paths.project_dir / ".nebius-cxcli" / "config.lock"):
        writes, removals, expected, manifest = prepare_ordinary_apps(
            config, paths, source_preimage=source_preimage
        )
        ProjectBundleTransaction(paths.project_dir).commit(
            writes, removals=removals, expected_preimages=expected
        )
        return [paths.generated_dir / name for name in manifest["render"]["ordinary_files"]]


def prepare_ordinary_apps(
    config: Any,
    paths: ProjectPaths,
    *,
    source_preimage: bytes | None = None,
    source_content: bytes | None = None,
    component_output_values=None,
):
    """Prepare an app-only transaction without publishing project files."""
    with ExitStack():
        manifest = load_generated_manifest(paths.generated_dir)
        validate_ordinary_app_scope(paths, manifest=manifest)
        # Keep the lifecycle's normalized protected runtime exactly as accepted;
        # only ordinary rows and their telemetry settings come from the candidate.
        candidate = copy.deepcopy(manifest["runtime_config"])
        candidate["apps"]["charts"] = [
            row
            for row in candidate.get("apps", {}).get("charts", [])
            if component_type_id(row) == "soperator" or "soperator_registration" in row
        ] + ordinary_config(config).get("apps", {}).get("charts", [])
        settings_by_target = {
            row.get("instance_id"): row.get("observability")
            for row in to_plain_data(config).get("deploy", {}).get("targets", [])
        }
        for row in candidate.get("deploy", {}).get("targets", []):
            settings = settings_by_target.get(row.get("instance_id"))
            if settings is not None:
                row["observability"] = copy.deepcopy(settings)
        materialize_observability_app_values(candidate)
        if protected_config_digest(candidate) != protected_config_digest(
            manifest["runtime_config"]
        ):
            raise RuntimeError(
                "Ordinary app materialization changed protected runtime configuration"
            )
        transaction = ProjectBundleTransaction(paths.project_dir)
        original_targets = (
            paths.config_path,
            manifest_path_for_generated_dir(paths.generated_dir),
            paths.reports_dir / BASELINE_FILENAME,
            *_protected_files(paths),
        )
        preimages = transaction.snapshot_preimages(original_targets, read_only=True)
        if source_preimage is not None and preimages[paths.config_path].content != source_preimage:
            raise RuntimeError("Authored config changed during ordinary app validation")
        current_files = set(_ordinary_files(paths, config))
        app_preimages = (
            transaction.snapshot_preimages(current_files, read_only=True) if current_files else {}
        )
        stage = staged_generated_paths(paths)
        try:
            from .deployment_state import DeploymentGeneration

            stage.generated_dir.rmdir()
            DeploymentGeneration.capture(paths, manifest).materialize(stage)
            for path in _ordinary_files(stage, candidate):
                path.unlink()
            written = render_flux(
                candidate,
                stage,
                ordinary_only=True,
                component_output_values=component_output_values,
            )
            for target_ref in enabled_cluster_target_refs(config):
                omit_shared_protected_resources(
                    flux_target_dir(stage, target_ref) / "ordinary",
                    flux_target_dir(paths, target_ref),
                )
                validate_resource_ownership(
                    flux_target_dir(stage, target_ref) / "ordinary",
                    flux_target_dir(paths, target_ref),
                )
            from .application_compatibility import refresh_application_manifest

            manifest = refresh_application_manifest(candidate, stage, manifest)
            new_files = {
                paths.generated_dir / p.relative_to(stage.generated_dir): p.read_bytes()
                for p in written
                if p.is_file()
            }
            # Every protected file and the authored config participate as unchanged
            # preimages, so a concurrent lifecycle writer cannot be overwritten.
            writes = {
                path: item.content for path, item in preimages.items() if item.content is not None
            }
            writes.update(new_files)
            if source_content is not None:
                writes[paths.config_path] = source_content
            manifest = copy.deepcopy(manifest)
            manifest["runtime_config"] = candidate
            manifest["render"]["app_scope"] = "ordinary"
            manifest["render"]["ordinary_validations"] = observability_validation_specs(candidate)
            manifest["render"]["ordinary_files"] = {
                p.relative_to(paths.generated_dir).as_posix(): _digest(content)
                for p, content in new_files.items()
            }
            from .compatibility_matrix import digest

            manifest["render"]["source_config_sha256"] = digest(
                yaml.safe_load(writes[paths.config_path])
            )
            writes[manifest_path_for_generated_dir(paths.generated_dir)] = _json_bytes(manifest)
            removals = current_files - set(new_files)
            expected = {
                **{path: "absent" for path in new_files if path not in app_preimages},
                **{path: item.sha256 for path, item in {**preimages, **app_preimages}.items()},
            }
            return writes, removals, expected, manifest
        finally:
            reset_generated_bundle(stage)


def validate_ordinary_bundle(paths: ProjectPaths, manifest: Mapping[str, Any]) -> dict[str, Any]:
    baseline = validate_ordinary_app_scope(paths, manifest=manifest)
    render = manifest.get("render", {})
    if render.get("app_scope") != "ordinary":
        raise RuntimeError("Render the ordinary app configuration before applying it")
    expected = render.get("ordinary_files")
    if not isinstance(expected, dict):
        raise RuntimeError("Ordinary app manifest is missing its generated inventory")
    actual = {
        path.relative_to(paths.generated_dir).as_posix(): _digest(path.read_bytes())
        for path in _ordinary_files(paths, manifest["runtime_config"])
    }
    if actual != expected:
        raise RuntimeError("Ordinary app generated bundle changed; render it again before apply")
    return baseline


def _live_json(argv: list[str], env: Mapping[str, str]) -> Any:
    result = kubernetes_process.run(
        argv, env={**os.environ, **env}, capture_output=True, text=True, timeout=90
    )
    if result.returncode:
        raise RuntimeError("Could not verify live app ownership before apply")
    return json.loads(result.stdout or "{}")


def _accepted_read_only_oci_source(desired, live, accepted, desired_releases, live_releases):
    """Reuse an unchanged accepted source without claiming or mutating its ownership."""
    identity = resource_identity(desired)
    prior = accepted.get(identity)
    owner = desired.get("metadata", {}).get("annotations", {}).get("cxcli.nebius.com/app-owner")
    meta, status = live.get("metadata", {}), live.get("status", {})
    ready = next((c for c in status.get("conditions", []) if c.get("type") == "Ready"), {})
    if (
        desired.get("kind") != "OCIRepository"
        or not prior
        or not owner
        or resource_identity(live) != identity
        or meta.get("annotations", {}).get("cxcli.nebius.com/app-owner")
        or prior.get("metadata", {}).get("annotations", {}).get("cxcli.nebius.com/app-owner")
        not in {None, owner}
        or meta.get("ownerReferences", []) != prior.get("metadata", {}).get("ownerReferences", [])
        or not meta.get("uid")
        or meta.get("deletionTimestamp")
        or not meta.get("generation")
        or ready.get("status") != "True"
        or ready.get("observedGeneration", status.get("observedGeneration")) != meta["generation"]
    ):
        return False

    def canonical_spec(document):
        spec = copy.deepcopy(document.get("spec", {}))
        # Flux source API defaults; all other fields remain exact.
        for key, value in (
            ("provider", "generic"),
            ("timeout", "60s"),
            ("insecure", False),
            ("suspend", False),
        ):
            spec.setdefault(key, value)
        return spec

    expected = canonical_spec(desired)
    if (
        expected != canonical_spec(prior)
        or expected != canonical_spec(live)
        or expected.get("suspend")
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(expected.get("ref", {}).get("digest", "")))
        or set(expected.get("ref", {})) != {"digest"}
    ):
        return False

    def uses_source(release):
        ref = release.get("spec", {}).get("chartRef", {})
        return (
            ref.get("kind") == "OCIRepository"
            and ref.get("name") == identity[3]
            and ref.get("namespace", release.get("metadata", {}).get("namespace")) == identity[2]
        )

    consumers = {resource_identity(r): r for r in desired_releases if uses_source(r)}
    live_consumers = {resource_identity(r): r for r in live_releases if uses_source(r)}
    accepted_consumers = {
        key
        for key, item in accepted.items()
        if item.get("kind") == "HelmRelease" and uses_source(item)
    }
    if (
        not consumers
        or consumers.keys() != live_consumers.keys()
        or consumers.keys() != accepted_consumers
    ):
        return False
    for key, consumer in consumers.items():
        for item in (consumer, live_consumers[key], accepted.get(key, {})):
            metadata = item.get("metadata", {})
            if (
                not uses_source(item)
                or metadata.get("annotations", {}).get("cxcli.nebius.com/app-owner") != owner
                or metadata.get("deletionTimestamp")
                or item.get("spec", {}).get("suspend")
            ):
                return False
        if not live_consumers[key].get("metadata", {}).get("uid"):
            return False
    return True


def validate_live_app_ownership(
    flux_dir: Path,
    *,
    extra_env: Mapping[str, str],
    accepted_documents: Mapping | None = None,
) -> None:
    docs = resource_documents(flux_dir)
    desired_releases = [doc for doc in docs if doc.get("kind") == "HelmRelease"]
    version = _live_json(["helm", "version", "--template", '{"version":"{{.Version}}"}'], extra_env)
    if not isinstance(version, Mapping) or not re.fullmatch(
        r"v4\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?", str(version.get("version", ""))
    ):
        raise RuntimeError("Ordinary app ownership checks require Helm 4")
    live_releases = _live_json(
        ["kubectl", "get", "helmreleases.helm.toolkit.fluxcd.io", "-A", "-o", "json"], extra_env
    ).get("items", [])
    # Helm 4 lists every release status by default; its removed --all flag fails
    # before this ownership boundary can inspect even an empty installation.
    helm_releases = _live_json(["helm", "list", "-A", "-o", "json"], extra_env)
    for desired in desired_releases:
        identity = resource_identity(desired)
        meta, spec = desired["metadata"], desired["spec"]
        helm_identity = (
            str(spec.get("targetNamespace") or meta.get("namespace", "")),
            str(spec.get("releaseName") or meta["name"]),
        )
        owned = False
        for live in live_releases:
            live_meta, live_spec = live.get("metadata", {}), live.get("spec", {})
            live_helm_identity = (
                str(live_spec.get("targetNamespace") or live_meta.get("namespace", "")),
                str(live_spec.get("releaseName") or live_meta.get("name", "")),
            )
            if identity == resource_identity(live):
                if live_meta.get("annotations", {}).get("cxcli.nebius.com/app-owner") != meta.get(
                    "annotations", {}
                ).get("cxcli.nebius.com/app-owner"):
                    raise RuntimeError(
                        "Ordinary app HelmRelease is already managed by another owner"
                    )
                owned = True
            elif live_helm_identity == helm_identity:
                raise RuntimeError(
                    "Ordinary app Helm release is already managed by another Flux resource"
                )
        if not owned and any(
            (item.get("namespace"), item.get("name")) == helm_identity for item in helm_releases
        ):
            raise RuntimeError(
                "Ordinary app Helm release already exists; automatic adoption is not supported"
            )
    external_secrets = [doc for doc in docs if doc.get("kind") == "ExternalSecret"]
    live_external_secrets = (
        _live_json(
            ["kubectl", "get", "externalsecrets.external-secrets.io", "-A", "-o", "json"], extra_env
        ).get("items", [])
        if external_secrets
        else []
    )
    shared_resources = set()
    for doc in docs:
        group, kind, namespace, name = resource_identity(doc)
        if kind == "HelmRelease":
            continue
        resource = f"{kind}.{group}" if group else kind
        argv = ["kubectl", "get", resource, name, "--ignore-not-found", "-o", "json"]
        if namespace:
            argv.extend(["-n", namespace])
        live = _live_json(argv, extra_env)
        desired_owner = (
            doc.get("metadata", {}).get("annotations", {}).get("cxcli.nebius.com/app-owner")
        )
        if not desired_owner:
            raise RuntimeError("Ordinary app resource is missing its owner identity")
        if kind == "ExternalSecret":
            secret_target = _external_secret_target(doc)
            if secret_target is None:
                raise RuntimeError("ExternalSecret target identity is missing")
            secret_namespace, secret_name = secret_target
            if any(
                _external_secret_target(item) == (secret_namespace, secret_name)
                and resource_identity(item) != resource_identity(doc)
                for item in live_external_secrets
            ):
                raise RuntimeError("Another ExternalSecret already owns the target Secret")
            secret = _live_json(
                [
                    "kubectl",
                    "get",
                    "secret",
                    secret_name,
                    "-n",
                    secret_namespace,
                    "--ignore-not-found",
                    "-o",
                    "json",
                ],
                extra_env,
            )
            if secret and not any(
                ref.get("kind") == "ExternalSecret"
                and ref.get("uid")
                and ref.get("uid") == live.get("metadata", {}).get("uid")
                for ref in secret.get("metadata", {}).get("ownerReferences", [])
            ):
                raise RuntimeError("Ordinary ExternalSecret target Secret has another owner")
        if not live:
            continue
        if kind == "Namespace" and all(
            live.get("metadata", {}).get("labels", {}).get(key) == value
            for key, value in doc.get("metadata", {}).get("labels", {}).items()
        ):
            shared_resources.add(resource_identity(doc))
            continue
        if _accepted_read_only_oci_source(
            doc, live, accepted_documents or {}, desired_releases, live_releases
        ):
            shared_resources.add(resource_identity(doc))
            continue
        if (
            live.get("metadata", {}).get("annotations", {}).get("cxcli.nebius.com/app-owner")
            != desired_owner
        ):
            raise RuntimeError(f"Ordinary app resource already has another owner: {kind}/{name}")

    # The caller supplied a private apply snapshot. Existing namespaces are
    # shared read-only prerequisites, as are proven unchanged accepted OCI inputs.
    # Omit them from this snapshot only; never adopt or relabel a shared resource.
    if shared_resources:
        kustomization_path = flux_dir / "kustomization.yaml"
        kustomization = yaml.safe_load(kustomization_path.read_text())
        removed = set()
        for path in flux_dir.glob("*.yaml"):
            if path == kustomization_path:
                continue
            remaining = [
                doc
                for doc in yaml.safe_load_all(path.read_text())
                if isinstance(doc, dict) and resource_identity(doc) not in shared_resources
            ]
            if remaining:
                path.write_text(yaml.safe_dump_all(remaining, sort_keys=False))
            else:
                path.unlink()
                removed.add(path.name)
        kustomization["resources"] = [
            name for name in kustomization.get("resources", []) if Path(name).name not in removed
        ]
        kustomization_path.write_text(yaml.safe_dump(kustomization, sort_keys=False))


def _validate_live_lifecycle_complete(
    identity: Mapping[str, str], env: Mapping[str, str], *, state=None
) -> None:
    digest = hashlib.sha256(identity["cluster_id"].encode()).hexdigest()
    prefixes = (
        f"nebius-cxcli-soperator-install-{digest[:20]}",
        f"nebius-cxcli-soperator-op-{digest[:10]}-",
    )
    from .installation_reconciliation import _read

    anchors = _read(["get", "configmaps", "-n", "kube-system", "-o", "json"], env)
    owned = {
        str(item.get("data", {}).get("operationId", "")): item.get("data", {})
        for item in anchors.get("items", [])
        if str(item.get("metadata", {}).get("name", "")).startswith(prefixes)
    }
    for initial in owned.values():
        data, seen = initial, set()
        while True:
            operation_id = data.get("operationId")
            if (
                data.get("clusterId") != identity["cluster_id"]
                or data.get("kubernetesUid") != identity["kubernetes_uid"]
                or operation_id in seen
            ):
                raise RuntimeError("Live Soperator operation identity or supersession is invalid")
            seen.add(operation_id)
            if data.get("status") == "complete":
                break
            if data.get("status") == "reconciled" and state is not None:
                from .installation_reconciliation import validate_reconciled

                matches = [item for item in anchors.get("items", []) if item.get("data") == data]
                if len(matches) != 1:
                    raise RuntimeError("Reconciled operation identity is ambiguous")
                validate_reconciled(matches[0], state, state.read())
                break
            successor = data.get("supersededBy")
            if data.get("status") != "superseded" or successor not in owned:
                raise RuntimeError(
                    "A live Soperator operation requires recovery before ordinary app mutation"
                )
            data = owned[successor]


def runtime_manifest_guard(
    owner: str, env: Mapping[str, str]
) -> Callable[[Mapping[str, Any]], dict[str, Any] | None]:
    def guard(doc: Mapping[str, Any]) -> dict[str, Any] | None:
        candidate = copy.deepcopy(dict(doc))
        _group, kind, namespace, name = resource_identity(candidate)
        argv = ["kubectl", "get", kind, name, "--ignore-not-found", "-o", "json"]
        if namespace:
            argv.extend(["-n", namespace])
        live = _live_json(argv, env)
        if (
            live
            and live.get("metadata", {}).get("annotations", {}).get("cxcli.nebius.com/app-owner")
            != owner
        ):
            if kind == "Namespace":
                return None  # Existing namespace is a read-only shared prerequisite.
            raise RuntimeError(f"Runtime app resource already has another owner: {kind}/{name}")
        candidate.setdefault("metadata", {}).setdefault("annotations", {})[
            "cxcli.nebius.com/app-owner"
        ] = owner
        return candidate

    return guard


def runtime_app_documents(config: Any, target_ref: str) -> list[dict[str, Any]]:
    from .grafana_database_runtime import runtime_secret_documents
    from .observability_runtime import runtime_secret_documents as telemetry_secret_documents

    identities = {
        (spec.namespace, name)
        for spec in grafana_release_specs(config, target_ref=target_ref)
        for name in (spec.admin_secret_name, spec.token_secret_name)
        if name
    }
    return (
        runtime_secret_documents(config, target_ref)
        + telemetry_secret_documents(config, target_ref)
        + [
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"namespace": namespace, "name": name},
            }
            for namespace, name in sorted(identities)
        ]
    )


@contextmanager
def runtime_app_mutations(
    config: Any, *, target_ref: str, env: Mapping[str, str], authority: Callable[[], object]
):
    owner = hashlib.sha256(
        json.dumps(
            [to_plain_data(config).get("client_info", {}), target_ref], sort_keys=True
        ).encode()
    ).hexdigest()
    guard = runtime_manifest_guard(owner, env)
    for doc in runtime_app_documents(config, target_ref):
        guard(doc)
    with app_mutation_scope(authority, guard):
        yield


@dataclass(frozen=True)
class OrdinaryAppServices:
    prepare_handoff: Callable[..., dict[str, str] | None]
    read_kubernetes_uid: Callable[..., str]
    ensure_grafana: Callable[..., Any]
    apply_flux: Callable[..., Any]
    collect_grafana: Callable[..., Sequence[dict[str, Any]]]
    emit: Callable[[str], Any]


def assert_accepted_deployment(
    config: Any,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
    baseline: Mapping[str, Any],
    target_refs: Sequence[str],
    *,
    assert_held: Callable[[], None],
):
    """Bind app effects to the current backend and accepted identity."""
    from .deployment_local import LocalObjectStore
    from .deployment_state import DeploymentState
    from .terraform_backend import backend_settings_from_config

    assert_held()
    settings = backend_settings_from_config(config)
    if manifest.get("execution", {}).get("backend") != asdict(settings):
        raise RuntimeError("Ordinary app bundle does not match the deployment backend")
    state = DeploymentState(
        LocalObjectStore.for_project(paths),
        settings,
        command="ordinary-apps",
        baseline_generation=str(baseline.get("deployment_generation") or ""),
        baseline_targets=target_refs,
        assert_held=assert_held,
    )
    record = state.read()
    validate_accepted_deployment_record(record.value if record else None, baseline, target_refs)
    assert_held()
    return state


def validate_accepted_deployment_record(record, baseline, target_refs) -> None:
    from .installation_reconciliation import pending_reconciliation

    if not isinstance(record, Mapping) or (
        record.get("active") is not None
        and not (len(target_refs) == 1 and pending_reconciliation(record, target_refs[0]))
    ):
        raise RuntimeError("Ordinary apps require a quiescent accepted deployment")
    accepted = record.get("accepted") or {}
    generation = baseline.get("deployment_generation")
    if not generation or accepted.get("generation") != generation:
        raise RuntimeError(
            "Ordinary app baseline does not match the accepted deployment generation"
        )
    evidence = accepted.get("evidence") or {}
    for ref in target_refs:
        identity = baseline.get("identities", {}).get(ref)
        if (
            not identity
            or identity != evidence.get("identities", {}).get(ref)
            or identity != evidence.get("targets", {}).get(ref, {}).get("identity")
        ):
            raise RuntimeError("Ordinary app target differs from the accepted deployment identity")


def apply_ordinary_apps(
    config: Any,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
    *,
    targets: Sequence[Mapping[str, str]],
    services: OrdinaryAppServices,
    validations: Sequence[Mapping[str, Any]] = (),
    assert_project_authority: Callable[[], None],
) -> list[dict[str, Any]]:
    with SoperatorOperationLocalLock(paths.project_dir / ".nebius-cxcli" / "config.lock"):
        baseline = validate_ordinary_bundle(paths, manifest)
        if not targets:
            raise RuntimeError("Ordinary app apply requires an explicit cluster target")
        refs = [str(target["target_ref"]) for target in targets]
        state = assert_accepted_deployment(
            config, paths, manifest, baseline, refs, assert_held=assert_project_authority
        )
        statuses: list[dict[str, Any]] = []
        for target in targets:
            target_ref = str(target["target_ref"])
            identity = baseline.get("identities", {}).get(target_ref, {})
            if not identity.get("cluster_id") or not identity.get("kubernetes_uid"):
                raise RuntimeError("The selected target has no accepted immutable cluster identity")
            with ExitStack() as stack:
                bound_target = {**target, "cluster_id": identity["cluster_id"]}
                # Always derive a temporary context from the accepted immutable ID.
                bound_target.pop("kube_context", None)
                env = services.prepare_handoff(
                    config,
                    paths,
                    stack=stack,
                    target=bound_target,
                    persist_local_kubeconfig=False,
                    set_current_context=False,
                    allow_terraform_output=False,
                )
                if not env:
                    raise RuntimeError("Could not establish the ordinary app target handoff")
                # The handoff environment names are shared with existing Grafana runtime.
                from .grafana_runtime import (
                    GRAFANA_TARGET_CLUSTER_ID_ENV,
                    GRAFANA_TARGET_KUBE_CONTEXT_ENV,
                )

                context = str(env.get(GRAFANA_TARGET_KUBE_CONTEXT_ENV, ""))
                if env.get(GRAFANA_TARGET_CLUSTER_ID_ENV) != identity["cluster_id"] or (
                    services.read_kubernetes_uid(kube_context=context, extra_env=env)
                    != identity["kubernetes_uid"]
                ):
                    raise RuntimeError(
                        "Ordinary app handoff does not match the accepted cluster identity"
                    )
                lease = stack.enter_context(
                    SoperatorOperationLease(
                        kube_context=context,
                        cluster_id=identity["cluster_id"],
                        operation_fingerprint=_digest(
                            _json_bytes(manifest["render"]["ordinary_files"])
                        ),
                        extra_env=env,
                    )
                )

                def assert_authority(lease=lease) -> None:
                    assert_project_authority()
                    lease.assert_held()

                assert_authority()
                from . import cli
                from .installation_reconciliation import reconcile_accepted_installation

                state.assert_held = assert_authority
                reconcile_accepted_installation(
                    cli, state, target_ref=target_ref, env=env, fence=assert_authority
                )
                _validate_live_lifecycle_complete(identity, env, state=state)
                validate_ordinary_bundle(paths, manifest)
                source_dir = flux_target_dir(paths, target_ref) / "ordinary"
                temp_dir = Path(
                    stack.enter_context(tempfile.TemporaryDirectory(prefix="cxcli-ordinary-apps-"))
                )
                for relative, expected in manifest["render"]["ordinary_files"].items():
                    source = paths.generated_dir / relative
                    if source.parent != source_dir:
                        continue
                    content = source.read_bytes()
                    if _digest(content) != expected:
                        raise RuntimeError("Ordinary app bundle changed during target handoff")
                    (temp_dir / source.name).write_bytes(content)
                validate_resource_ownership(temp_dir, flux_target_dir(paths, target_ref))
                from .deployment_jail_state import accepted_effective_generation
                from .deployment_observation import target_documents

                accepted_target = state.read().value["accepted"]["evidence"]["targets"][target_ref]
                accepted_generation = accepted_effective_generation(
                    state, target_ref, accepted_target
                )
                accepted_documents = (
                    {
                        resource_identity(doc): doc
                        for doc in target_documents(accepted_generation, target_ref).values()
                    }
                    if accepted_generation is not None
                    else {}
                )
                validate_live_app_ownership(
                    temp_dir, extra_env=env, accepted_documents=accepted_documents
                )
                target_config = ordinary_config(config)
                owner = hashlib.sha256(
                    json.dumps(
                        [to_plain_data(config).get("client_info", {}), target_ref], sort_keys=True
                    ).encode()
                ).hexdigest()
                guard = runtime_manifest_guard(owner, env)
                for doc in runtime_app_documents(config, target_ref):
                    guard(doc)
                stack.enter_context(app_mutation_scope(assert_authority, guard))
                assert_authority()
                # Keep Soperator signal context for Grafana datasource generation.
                from .grafana_cluster import preflight_dashboard_ownership
                from .grafana_database_runtime import preflight_grafana_database
                from .nsight_runtime import prepare_nsight_viewers

                preflight_grafana_database(config, target_ref=target_ref, extra_env=env)
                preflight_dashboard_ownership(config, target=target_ref, env=env)
                prepare_nsight_viewers(config, extra_env=env, target_ref=target_ref)
                services.ensure_grafana(config, extra_env=env, target_ref=target_ref)
                assert_authority()
                services.apply_flux(
                    replace(paths, flux_dir=temp_dir),
                    config=target_config,
                    require_existing_flux=True,
                    extra_env=env,
                    target_ref=target_ref,
                    assert_authority=assert_authority,
                )
                assert_authority()
                from .grafana_cluster import replay_dashboards

                replay_dashboards(config, paths, target=target_ref, env=env)
                target_validations = [
                    dict(item) for item in validations if item.get("target_ref") == target_ref
                ]
                if target_validations:
                    run_observability_validations(
                        target_validations,
                        reports_dir=paths.reports_dir,
                        extra_env=env,
                        emit=services.emit,
                    )
                statuses.extend(
                    services.collect_grafana(config, extra_env=env, target_ref=target_ref)
                )
                from .nsight_runtime import collect_nsight_status

                collect_nsight_status(
                    config, extra_env=env, target_ref=target_ref, emit=services.emit
                )
                services.emit(
                    f"Ordinary apps applied for target {target_ref}; infrastructure unchanged."
                )
        return statuses


@dataclass(frozen=True)
class OrdinaryAppWorkflow:
    """Bind the existing CLI services to ordinary app and acceptance workflows."""

    services: Callable[[], OrdinaryAppServices]
    resolve_targets: Callable[..., list[dict[str, str]]]

    def accept_baseline(
        self,
        paths: ProjectPaths,
        *,
        identities: Mapping[str, Mapping[str, str]] | None = None,
        selected_target_refs: Sequence[str] | None = None,
        deployment_generation: str = "",
        expected_generation: Any = None,
    ) -> bool:
        """Capture all accepted target bindings at successful lifecycle completion."""
        services = self.services()
        manifest = load_generated_manifest(paths.generated_dir)
        config = runtime_config_from_manifest(manifest)
        bindings = {key: dict(value) for key, value in (identities or {}).items()}
        for target in self.resolve_targets(manifest, requested_target_ref=None, all_targets=True):
            target_ref = str(target["target_ref"])
            if target_ref in bindings or (
                selected_target_refs is not None and target_ref not in selected_target_refs
            ):
                continue
            with ExitStack() as stack:
                bound_target = dict(target)
                bound_target.pop("kube_context", None)
                env = services.prepare_handoff(
                    config,
                    paths,
                    stack=stack,
                    target=bound_target,
                    persist_local_kubeconfig=False,
                    set_current_context=False,
                )
                cluster_id = str((env or {}).get(GRAFANA_TARGET_CLUSTER_ID_ENV) or "").strip()
                context = str((env or {}).get(GRAFANA_TARGET_KUBE_CONTEXT_ENV) or "").strip()
                if not cluster_id or not context:
                    raise RuntimeError("Lifecycle completion could not bind every app target")
                uid = services.read_kubernetes_uid(kube_context=context, extra_env=env)
                if not uid:
                    raise RuntimeError("Lifecycle completion could not verify an app target UID")
                bindings[target_ref] = {"cluster_id": cluster_id, "kubernetes_uid": uid}
        return accept_ordinary_app_baseline(
            paths,
            identities=bindings,
            selected_target_refs=selected_target_refs,
            deployment_generation=deployment_generation,
            expected_generation=expected_generation,
        )

    def apply(
        self,
        config: Any,
        paths: ProjectPaths,
        manifest: Mapping[str, Any],
        *,
        target_ref: str | None,
        all_targets: bool,
        validations: Sequence[Mapping[str, Any]] = (),
        assert_project_authority: Callable[[], None],
    ) -> None:
        statuses = apply_ordinary_apps(
            config,
            paths,
            manifest,
            targets=self.resolve_targets(
                manifest, requested_target_ref=target_ref, all_targets=all_targets
            ),
            validations=validations,
            services=self.services(),
            assert_project_authority=assert_project_authority,
        )

        if statuses:
            write_grafana_status(paths, statuses, preserve_existing=True)
