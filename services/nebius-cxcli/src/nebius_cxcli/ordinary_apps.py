"""Ordinary app generation on clusters whose infrastructure has a lifecycle owner.

The accepted baseline is written only by successful lifecycle operations.
Ordinary commands compare it and publish only app resources and their manifest.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

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
from .project_bundle_transaction import ProjectBundleTransaction
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
    for target in payload.get("deploy", {}).get("targets", []):
        # This field controls extra app telemetry, not Soperator's exporters.
        if isinstance(target, dict):
            target.pop("observability", None)
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
    paths: ProjectPaths, candidate: Mapping[str, Any], *, expected_bytes: bytes | None = None
) -> bool:
    """Publish only app configuration, preserving authored protected subtrees."""
    with SoperatorOperationLocalLock(paths.project_dir / ".nebius-cxcli" / "config.lock"):
        validate_ordinary_app_scope(paths)
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
        if protected_config_digest(updated) != protected_config_digest(source):
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
) -> None:
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
    targets = (paths.config_path, manifest_path, baseline_path, *_protected_files(paths))
    preimages = transaction.snapshot_preimages(targets)
    manifest_bytes = preimages[manifest_path].content
    source_bytes = preimages[paths.config_path].content
    if manifest_bytes is None or source_bytes is None:
        raise RuntimeError("Lifecycle acceptance lost its generated manifest or source")
    accepted_manifest = json.loads(manifest_bytes)
    if accepted_manifest != manifest:
        raise RuntimeError("Generated manifest changed during lifecycle acceptance")
    baseline = {
        "schema": "nebius-cxcli-ordinary-apps/v1",
        "source_sha256": protected_config_digest(yaml.safe_load(source_bytes)),
        "runtime_sha256": protected_config_digest(accepted_manifest["runtime_config"]),
        "protected_files": {
            path.relative_to(paths.generated_dir).as_posix(): item.sha256
            for path, item in preimages.items()
            if path not in {paths.config_path, manifest_path, baseline_path}
        },
        "identities": bindings,
        "ordinary_files": {
            path.relative_to(paths.generated_dir).as_posix(): _digest(path.read_bytes())
            for path in _ordinary_files(paths, manifest["runtime_config"])
        },
    }
    writes = {path: item.content for path, item in preimages.items() if item.content is not None}
    writes[baseline_path] = _json_bytes(baseline)
    transaction.commit(writes, expected_preimages={p: item.sha256 for p, item in preimages.items()})


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
            if existing is not None:
                if doc.get("kind") == "Namespace" and not doc.get("metadata", {}).get("labels"):
                    continue
                if doc.get("kind") in {
                    "HelmRepository",
                    "GitRepository",
                    "OCIRepository",
                } and doc.get("spec") == existing.get("spec"):
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
    """Publish apps only, retaining exact protected resources and accepted hashes."""
    with SoperatorOperationLocalLock(paths.project_dir / ".nebius-cxcli" / "config.lock"):
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
        preimages = transaction.snapshot_preimages(original_targets)
        if source_preimage is not None and preimages[paths.config_path].content != source_preimage:
            raise RuntimeError("Authored config changed during ordinary app validation")
        current_files = set(_ordinary_files(paths, config))
        app_preimages = transaction.snapshot_preimages(current_files) if current_files else {}
        stage = staged_generated_paths(paths)
        try:
            written = render_flux(candidate, stage, ordinary_only=True)
            for target_ref in enabled_cluster_target_refs(config):
                omit_shared_protected_resources(
                    flux_target_dir(stage, target_ref) / "ordinary",
                    flux_target_dir(paths, target_ref),
                )
                validate_resource_ownership(
                    flux_target_dir(stage, target_ref) / "ordinary",
                    flux_target_dir(paths, target_ref),
                )
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
            manifest = copy.deepcopy(manifest)
            manifest["runtime_config"] = candidate
            manifest["render"]["app_scope"] = "ordinary"
            manifest["render"]["ordinary_validations"] = observability_validation_specs(candidate)
            manifest["render"]["ordinary_files"] = {
                p.relative_to(paths.generated_dir).as_posix(): _digest(content)
                for p, content in new_files.items()
            }
            writes[manifest_path_for_generated_dir(paths.generated_dir)] = _json_bytes(manifest)
            removals = current_files - set(new_files)
            transaction.commit(
                writes,
                removals=removals,
                expected_preimages={
                    **{path: "absent" for path in new_files if path not in app_preimages},
                    **{path: item.sha256 for path, item in {**preimages, **app_preimages}.items()},
                },
            )
            return sorted(new_files)
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
    result = subprocess.run(
        argv, env={**os.environ, **env}, capture_output=True, text=True, timeout=90
    )
    if result.returncode:
        raise RuntimeError("Could not verify live app ownership before apply")
    return json.loads(result.stdout or "{}")


def validate_live_app_ownership(flux_dir: Path, *, extra_env: Mapping[str, str]) -> None:
    docs = resource_documents(flux_dir)
    desired_releases = [doc for doc in docs if doc.get("kind") == "HelmRelease"]
    live_releases = _live_json(
        ["kubectl", "get", "helmreleases.helm.toolkit.fluxcd.io", "-A", "-o", "json"], extra_env
    ).get("items", [])
    helm_releases = _live_json(["helm", "list", "-A", "--all", "-o", "json"], extra_env)
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
    shared_namespaces = set()
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
            shared_namespaces.add(resource_identity(doc))
            continue
        if (
            live.get("metadata", {}).get("annotations", {}).get("cxcli.nebius.com/app-owner")
            != desired_owner
        ):
            raise RuntimeError(f"Ordinary app resource already has another owner: {kind}/{name}")

    # The caller supplied a private apply snapshot. Existing namespaces are
    # shared read-only prerequisites; omit them from this snapshot only.
    if shared_namespaces:
        kustomization_path = flux_dir / "kustomization.yaml"
        kustomization = yaml.safe_load(kustomization_path.read_text())
        removed = set()
        for path in flux_dir.glob("*.yaml"):
            if path == kustomization_path:
                continue
            remaining = [
                doc
                for doc in yaml.safe_load_all(path.read_text())
                if isinstance(doc, dict) and resource_identity(doc) not in shared_namespaces
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


def _validate_live_lifecycle_complete(identity: Mapping[str, str], env: Mapping[str, str]) -> None:
    digest = hashlib.sha256(identity["cluster_id"].encode()).hexdigest()
    prefixes = (
        f"nebius-cxcli-soperator-install-{digest[:20]}",
        f"nebius-cxcli-soperator-op-{digest[:10]}-",
    )
    anchors = _live_json(["kubectl", "get", "configmaps", "-n", "kube-system", "-o", "json"], env)
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
    identities = {
        (spec.namespace, name)
        for spec in grafana_release_specs(config, target_ref=target_ref)
        for name in (spec.admin_secret_name, spec.token_secret_name)
        if name
    }
    return [
        {"apiVersion": "v1", "kind": "Secret", "metadata": {"namespace": namespace, "name": name}}
        for namespace, name in sorted(identities)
    ]


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


def apply_ordinary_apps(
    config: Any,
    paths: ProjectPaths,
    manifest: Mapping[str, Any],
    *,
    targets: Sequence[Mapping[str, str]],
    services: OrdinaryAppServices,
    validations: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    with SoperatorOperationLocalLock(paths.project_dir / ".nebius-cxcli" / "config.lock"):
        baseline = validate_ordinary_bundle(paths, manifest)
        if not targets:
            raise RuntimeError("Ordinary app apply requires an explicit cluster target")
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
                lease.assert_held()
                _validate_live_lifecycle_complete(identity, env)
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
                validate_live_app_ownership(temp_dir, extra_env=env)
                target_config = ordinary_config(config)
                owner = hashlib.sha256(
                    json.dumps(
                        [to_plain_data(config).get("client_info", {}), target_ref], sort_keys=True
                    ).encode()
                ).hexdigest()
                guard = runtime_manifest_guard(owner, env)
                for doc in runtime_app_documents(config, target_ref):
                    guard(doc)
                stack.enter_context(app_mutation_scope(lease.assert_held, guard))
                lease.assert_held()
                # Keep Soperator signal context for Grafana datasource generation.
                services.ensure_grafana(config, extra_env=env, target_ref=target_ref)
                lease.assert_held()
                services.apply_flux(
                    replace(paths, flux_dir=temp_dir),
                    config=target_config,
                    require_existing_flux=True,
                    extra_env=env,
                    target_ref=target_ref,
                    assert_authority=lease.assert_held,
                )
                lease.assert_held()
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
        self, paths: ProjectPaths, *, identities: Mapping[str, Mapping[str, str]] | None = None
    ) -> None:
        """Capture all accepted target bindings at successful lifecycle completion."""
        services = self.services()
        manifest = load_generated_manifest(paths.generated_dir)
        config = runtime_config_from_manifest(manifest)
        bindings = {key: dict(value) for key, value in (identities or {}).items()}
        for target in self.resolve_targets(manifest, requested_target_ref=None, all_targets=True):
            target_ref = str(target["target_ref"])
            if target_ref in bindings:
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
        accept_ordinary_app_baseline(paths, identities=bindings)

    def apply(
        self,
        config: Any,
        paths: ProjectPaths,
        manifest: Mapping[str, Any],
        *,
        target_ref: str | None,
        all_targets: bool,
        validations: Sequence[Mapping[str, Any]] = (),
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
        )

        if statuses:
            write_grafana_status(paths, statuses, preserve_existing=True)
