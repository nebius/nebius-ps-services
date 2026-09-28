"""Read-only admission and runtime-only credentials for persistent Grafana.

Admission is deliberately separate from dashboard ownership: zero dashboards
must not turn a SQLite deployment into an implicit database migration.
"""

from __future__ import annotations

import base64
import configparser
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import kubernetes_process
from .app_mutation import assert_app_mutation_authority, guarded_app_manifest
from .grafana_database import (
    OWNER_ANNOTATION,
    app_owner,
    database_identity,
    database_rows,
    database_secret_names,
    grafana_database_values,
    validate_database_values,
    validate_grafana_startup,
)
from .grafana_runtime import (
    _ensure_namespace,
    _kubectl_command,
    _kubectl_env,
    grafana_release_specs,
)
from .runtime_config import to_plain_data


@dataclass(frozen=True)
class RuntimeSecret:
    namespace: str
    name: str
    keys: tuple[str, ...]
    persisted: bool
    user_key: str = ""
    user: str = ""


def _read(
    kind: str, namespace: str, name: str, env: Mapping[str, str] | None, *, selector: str = ""
) -> dict[str, Any]:
    args = (["-n", namespace] if namespace else []) + ["get", kind]
    if name:
        args += [name, "--ignore-not-found"]
    if selector:
        args += ["-l", selector]
    args += ["-o", "json"]
    result = kubernetes_process.run(
        _kubectl_command(args, extra_env=env),
        env=_kubectl_env(env),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode:
        # Kubernetes errors may echo request bodies; never print Secret responses.
        raise RuntimeError(f"Cannot inspect {kind} for persistent Grafana admission")
    try:
        value = json.loads(result.stdout or "{}")
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"Invalid {kind} response during persistent Grafana admission") from exc


def _owned(resource: Mapping[str, Any], owner: str, label: str) -> None:
    metadata = resource.get("metadata", {})
    if metadata.get("annotations", {}).get(OWNER_ANNOTATION) != owner or metadata.get(
        "deletionTimestamp"
    ):
        raise RuntimeError(
            f"Persistent Grafana resource has missing or different ownership: {label}"
        )


def _matches(resource: Mapping[str, Any], release: str) -> bool:
    metadata = resource.get("metadata", {})
    return (
        metadata.get("name") == release
        or metadata.get("labels", {}).get("app.kubernetes.io/instance") == release
        or metadata.get("annotations", {}).get("meta.helm.sh/release-name") == release
    )


def _database_backend(
    deployment: Mapping[str, Any],
    namespace: str,
    env: Mapping[str, str] | None,
    *,
    config_map_name: str,
    password_secret: str,
    encryption_secret: str,
    config_map: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    pod = deployment.get("spec", {}).get("template", {}).get("spec", {})
    candidates = [item for item in pod.get("containers", []) if item.get("name") == "grafana"]
    if len(candidates) != 1:
        return {}
    environment = candidates[0].get("env", [])
    variables = {item.get("name"): item for item in environment}
    protected = [
        item.get("name", "")
        for item in environment
        if item.get("name", "").startswith(
            ("GF_DATABASE_", "GF_SECURITY_SECRET_KEY", "GF_PATHS_CONFIG")
        )
    ]
    if len(protected) != len(set(protected)):
        return {}
    try:
        validate_grafana_startup({**candidates[0], "env": variables})
    except ValueError:
        return {}
    # Unknown envFrom could override the file configuration.
    if candidates[0].get("envFrom"):
        return {}
    if any(key.startswith("GF_DATABASE_") and key != "GF_DATABASE_PASSWORD" for key in variables):
        return {}
    for key, name, secret_key in (
        ("GF_DATABASE_PASSWORD", password_secret, "password"),
        ("GF_SECURITY_SECRET_KEY", encryption_secret, "secret-key"),
    ):
        if variables.get(key, {}).get("valueFrom", {}).get("secretKeyRef") != {
            "name": name,
            "key": secret_key,
        }:
            return {}
    if "GF_SECURITY_SECRET_KEY__FILE" in variables:
        return {}
    # Inspect the file actually consumed, never an unrelated ConfigMap volume.
    config_path = "/etc/grafana/grafana.ini"
    mounts = [
        mount
        for mount in candidates[0].get("volumeMounts", [])
        if mount.get("mountPath") == config_path
        or config_path.startswith(str(mount.get("mountPath", "")).rstrip("/") + "/")
    ]
    if (
        len(mounts) != 1
        or any(
            mounts[0].get(key) != value
            for key, value in (
                ("name", "config"),
                ("mountPath", config_path),
                ("subPath", "grafana.ini"),
            )
        )
        or mounts[0].get("subPathExpr")
    ):
        return {}
    volumes = [volume for volume in pod.get("volumes", []) if volume.get("name") == "config"]
    if len(volumes) != 1:
        return {}
    source = volumes[0].get("configMap", {})
    if source.get("name") != config_map_name or source.get("items"):
        return {}
    if config_map is None:
        config_map = _read("configmap", namespace, config_map_name, env)
    config = config_map.get("data", {}).get("grafana.ini")
    if config is None:
        return {}
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(config)
        return dict(parser.items("database")) if parser.has_section("database") else {}
    except configparser.Error:
        return {}


def _inventory(config: Any, target_ref: str, env: Mapping[str, str] | None) -> list[RuntimeSecret]:
    databases = database_rows(config, target_ref)
    grafanas = grafana_release_specs(config, target_ref=target_ref)
    if not databases and not grafanas:
        return []
    if grafanas and len(databases) != 1:
        raise RuntimeError(
            "Managed Grafana requires exactly one PostgreSQL component on its target"
        )
    owner = app_owner(config, target_ref)
    namespaces = {database_identity(row)[0] for row in databases} | {
        spec.namespace for spec in grafanas
    }
    inventory = {
        ns: {
            kind: _read(kind, ns, "", env).get("items", [])
            for kind in ("deployments", "statefulsets", "persistentvolumeclaims")
        }
        for ns in namespaces
    }
    has_flux = bool(
        _read("customresourcedefinition", "", "helmreleases.helm.toolkit.fluxcd.io", env)
    )
    releases = {
        ns: _read("helmreleases", ns, "", env).get("items", []) if has_flux else []
        for ns in namespaces
    }
    specs: list[RuntimeSecret] = []
    for row in databases:
        validate_database_values(row.get("values", {}))
        namespace, release = database_identity(row)
        storage_class = row.get("values", {}).get("persistence", {}).get("storageClass", "")
        if not storage_class or not _read("storageclass", "", storage_class, env):
            raise RuntimeError(
                "Managed PostgreSQL requires an existing explicit StorageClass before deployment"
            )
        resources = [
            item
            for kind in ("statefulsets", "persistentvolumeclaims")
            for item in inventory[namespace][kind]
            if _matches(item, release)
            or item.get("metadata", {}).get("name")
            == f"{row.get('values', {}).get('persistence', {}).get('volumeName', 'data')}-{release}-0"
        ]
        released = bool(
            _read("secrets", namespace, "", env, selector=f"owner=helm,name={release}").get("items")
        )
        resources += [item for item in releases[namespace] if _matches(item, release)]
        for resource in resources:
            _owned(resource, owner, f"{resource.get('kind', 'database')}/{release}")
        for name in database_secret_names(row):
            specs.append(RuntimeSecret(namespace, name, ("password",), bool(resources) or released))
    for spec in grafanas:
        database_namespace, database_release = database_identity(databases[0])
        expected = {
            "type": "postgres",
            "host": f"{database_release}.{database_namespace}.svc:5432",
            "name": "grafana",
            "user": "grafana",
            "ssl_mode": "disable",
        }
        deployments = [
            item
            for item in inventory[spec.namespace]["deployments"]
            if _matches(item, spec.release_name)
        ]
        release_storage = _read(
            "secrets", spec.namespace, "", env, selector=f"owner=helm,name={spec.release_name}"
        ).get("items", [])
        released = bool(release_storage)
        helm_releases = [
            item for item in releases[spec.namespace] if _matches(item, spec.release_name)
        ]
        for release in helm_releases:
            _owned(release, owner, f"HelmRelease/{spec.release_name}")
            release_spec = release.get("spec", {})
            values = release_spec.get("values", {})
            try:
                canonical = grafana_database_values(
                    to_plain_data(config),
                    {
                        "target_ref": target_ref,
                        "namespace": spec.namespace,
                        "release-name": spec.release_name,
                        "values": values,
                    },
                )
            except ValueError:
                raise RuntimeError(
                    "Existing Grafana configuration is indeterminate; no automatic migration"
                ) from None
            env_refs = values.get("envValueFrom", {})
            if (
                release_spec.get("valuesFrom")
                or values.get("grafana.ini", {}).get("database", {}) != expected
                or any(
                    env_refs.get(key) != canonical["values.envValueFrom"][key]
                    for key in ("GF_DATABASE_PASSWORD", "GF_SECURITY_SECRET_KEY")
                )
                or any(
                    key.startswith(("GF_DATABASE_", "GF_SECURITY_SECRET_KEY"))
                    and key not in {"GF_DATABASE_PASSWORD", "GF_SECURITY_SECRET_KEY"}
                    for key in env_refs
                )
            ):
                raise RuntimeError(
                    "Existing Grafana uses SQLite or an indeterminate backend; no automatic migration"
                )
        other_workloads = [
            item
            for item in inventory[spec.namespace]["statefulsets"]
            if _matches(item, spec.release_name)
        ]
        if other_workloads or len(deployments) > 1:
            raise RuntimeError("Existing Grafana backend is indeterminate; no automatic migration")
        if released and not deployments:
            from .grafana_install_recovery import failed_initial_documents

            deployment, config_map = failed_initial_documents(
                helm_releases,
                release_storage,
                namespace=spec.namespace,
                name=spec.release_name,
                owner=owner,
            )
            if (
                _read(
                    "pods,replicasets",
                    spec.namespace,
                    "",
                    env,
                    selector=f"app.kubernetes.io/instance={spec.release_name}",
                ).get("items")
                or _database_backend(
                    deployment,
                    spec.namespace,
                    env,
                    config_map_name=spec.release_name,
                    password_secret=database_secret_names(databases[0])[1],
                    encryption_secret=f"{spec.release_name}-encryption",
                    config_map=config_map,
                )
                != expected
            ):
                raise RuntimeError(
                    "Failed Grafana install has ambiguous backend evidence; no automatic migration"
                )
        for deployment in deployments:
            backend = _database_backend(
                deployment,
                spec.namespace,
                env,
                config_map_name=spec.release_name,
                password_secret=database_secret_names(databases[0])[1],
                encryption_secret=f"{spec.release_name}-encryption",
            )
            if backend != expected:
                raise RuntimeError(
                    "Existing Grafana uses SQLite or an indeterminate backend; "
                    "PostgreSQL deployment supports new installations only (no automatic migration)"
                )
            _owned(deployment, owner, f"Deployment/{spec.release_name}")
        persisted = bool(deployments or helm_releases) or any(item.persisted for item in specs)
        specs += [
            RuntimeSecret(
                spec.namespace,
                spec.admin_secret_name,
                (spec.admin_user_key, spec.admin_password_key),
                persisted,
                spec.admin_user_key,
                spec.admin_user,
            ),
            RuntimeSecret(
                spec.namespace, f"{spec.release_name}-encryption", ("secret-key",), persisted
            ),
        ]
        if spec.token_secret_name:
            specs.append(
                RuntimeSecret(spec.namespace, spec.token_secret_name, (spec.token_key,), persisted)
            )
    return specs


def runtime_secret_documents(config: Any, target_ref: str) -> list[dict[str, Any]]:
    identities = {
        (database_identity(row)[0], name)
        for row in database_rows(config, target_ref)
        for name in database_secret_names(row)
    }
    identities.update(
        (spec.namespace, f"{spec.release_name}-encryption")
        for spec in grafana_release_specs(config, target_ref=target_ref)
    )
    return [
        {"apiVersion": "v1", "kind": "Secret", "metadata": {"namespace": ns, "name": name}}
        for ns, name in sorted(identities)
    ]


def _admit(config: Any, target_ref: str, env: Mapping[str, str] | None) -> list[RuntimeSecret]:
    specs = _inventory(config, target_ref, env)
    owner = app_owner(config, target_ref)
    missing = []
    # Check the *whole* inventory before generating or publishing any credential.
    for spec in specs:
        live = _read("secret", spec.namespace, spec.name, env)
        if not live:
            if spec.persisted:
                raise RuntimeError(
                    f"Persistent Grafana credential is missing: Secret/{spec.name}; refusing regeneration"
                )
            missing.append(spec)
            continue
        _owned(live, owner, f"Secret/{spec.name}")
        try:
            data = {
                key: base64.b64decode(live.get("data", {})[key], validate=True) for key in spec.keys
            }
            if not all(data.values()) or (
                spec.user_key and data[spec.user_key].decode() != spec.user
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(
                f"Incomplete persistent Grafana credential: Secret/{spec.name}; refusing replacement"
            ) from exc
    return missing


def preflight_grafana_database(
    config: Any, *, target_ref: str, extra_env: Mapping[str, str] | None
) -> None:
    """Read-only admission before any application/runtime preparation."""
    _admit(config, target_ref, extra_env)


def ensure_database_runtime_secrets(
    config: Any, *, target_ref: str, extra_env: Mapping[str, str] | None
) -> None:
    missing = _admit(config, target_ref, extra_env)
    token_names = {
        (spec.namespace, spec.token_secret_name)
        for spec in grafana_release_specs(config, target_ref=target_ref)
    }
    owner = app_owner(config, target_ref)
    for spec in missing:
        # The existing IAM delivery/compensation owner creates read tokens.
        if (spec.namespace, spec.name) in token_names:
            continue
        assert_app_mutation_authority()
        _ensure_namespace(spec.namespace, extra_env=extra_env)
        manifest = guarded_app_manifest(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "type": "Opaque",
                "metadata": {
                    "namespace": spec.namespace,
                    "name": spec.name,
                    "annotations": {OWNER_ANNOTATION: owner},
                },
                "stringData": {
                    key: spec.user if key == spec.user_key else secrets.token_urlsafe(48)
                    for key in spec.keys
                },
            }
        )
        if manifest is None:
            raise RuntimeError("Persistent Grafana Secret creation was not admitted")
        # Conditional create: a racing writer never gets overwritten by apply.
        result = kubernetes_process.run(
            _kubectl_command(["create", "-f", "-"], extra_env=extra_env),
            env=_kubectl_env(extra_env),
            input=json.dumps(manifest),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError(
                "Persistent Grafana Secret creation failed; rerun admission before retrying"
            )


def ensure_persistent_grafana(
    config: Any, *, target_ref: str, extra_env: Mapping[str, str] | None, emit: Any = None
) -> None:
    from .app_mutation import current_app_mutation_authority
    from .grafana_runtime import ensure_grafana_runtime_secrets
    from .ordinary_apps import runtime_app_mutations

    # Every deployment entry point uses the same ownership guard, including
    # the read-token IAM delivery adapter. Bootstrap supports standalone PG.
    with runtime_app_mutations(
        config,
        target_ref=target_ref,
        env=extra_env or {},
        authority=current_app_mutation_authority() or (lambda: None),
    ):
        ensure_database_runtime_secrets(config, target_ref=target_ref, extra_env=extra_env)
        ensure_grafana_runtime_secrets(
            config, target_ref=target_ref, extra_env=extra_env, emit=emit
        )


def verify_database_runtime(
    config: Any, *, target_ref: str, extra_env: Mapping[str, str] | None
) -> None:
    """Assert each selected replica passed its own database-backed readiness probe."""
    for row in database_rows(config, target_ref):
        namespace, release = database_identity(row)
        statefulset = _read("statefulset", namespace, release, extra_env)
        status = statefulset.get("status", {})
        if (
            status.get("observedGeneration", 0)
            < statefulset.get("metadata", {}).get("generation", 1)
            or status.get("readyReplicas") != 1
        ):
            raise RuntimeError("PostgreSQL authenticated readiness has not completed")
    if not database_rows(config, target_ref):
        return
    for spec in grafana_release_specs(config, target_ref=target_ref):
        deployment = _read("deployment", spec.namespace, spec.release_name, extra_env)
        expected = deployment.get("spec", {}).get("replicas", 2)
        status = deployment.get("status", {})
        pods = _read(
            "pods",
            spec.namespace,
            "",
            extra_env,
            selector=f"app.kubernetes.io/instance={spec.release_name}",
        ).get("items", [])
        ready = [
            pod
            for pod in pods
            if not pod.get("metadata", {}).get("deletionTimestamp")
            and any(
                item.get("type") == "Ready" and item.get("status") == "True"
                for item in pod.get("status", {}).get("conditions", [])
            )
        ]
        if (
            status.get("observedGeneration", 0)
            < deployment.get("metadata", {}).get("generation", 1)
            or status.get("updatedReplicas", 0) != expected
            or len(ready) < expected
        ):
            raise RuntimeError(
                "Every Grafana replica must pass its database-backed readiness probe"
            )
