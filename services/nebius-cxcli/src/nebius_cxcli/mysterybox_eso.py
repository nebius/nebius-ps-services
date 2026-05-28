"""Native External Secrets Operator integration for Nebius MysteryBox."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from .component_defaults import resolve_component_defaults
from .component_instances import (
    INSTANCE_ID_FIELD,
    component_instance_id,
    component_type_id,
    normalize_component_token,
)
from .component_wiring import component_output_ref
from .components import ComponentEntry, component_entries
from .deploy_targets import (
    TARGET_REF_FIELD,
    app_chart_target_ref,
    enabled_cluster_target_refs,
    target_scoped_app_instance_id,
)
from .runtime_config import to_plain_data
from .slack_notifier_runtime import soperator_notifier_mysterybox_secret_refs

EXTERNAL_SECRETS_APP_ID = "external-secrets"
MYSTERYBOX_INFRA_COMPONENT_ID = "mysterybox"
MYSTERYBOX_ESO_CONNECTIVITY_VALIDATION_KIND = "mysterybox_eso_connectivity"
MYSTERYBOX_ESO_MANAGED_LABEL = "nebius-cxcli.io/managed"
MYSTERYBOX_ESO_MANAGED_VALUE = "mysterybox-eso"
MYSTERYBOX_ESO_SOURCE_LABEL = "secrets.nebius.com/source"
MYSTERYBOX_ESO_SOURCE_VALUE = "mysterybox"

DEFAULT_STORE_NAME = "nebius-mysterybox-shared"
DEFAULT_API_DOMAIN = "api.nebius.cloud:443"
DEFAULT_CREDENTIAL_SECRET_NAME = "nebius-mysterybox-shared-creds"
DEFAULT_CREDENTIAL_SECRET_NAMESPACE = "external-secrets"
DEFAULT_CREDENTIAL_SECRET_KEY = "credentials.json"
DEFAULT_SYNC_NAMESPACE = "default"
DEFAULT_REFRESH_INTERVAL = "15m"
MYSTERYBOX_ESO_AUTO_PRIMARY_VERSION_POLICY = "auto-primary-version-pinning"
MYSTERYBOX_ESO_MANUAL_VERSION_POLICY = "manual-version-pinning"
MYSTERYBOX_ESO_VERSION_POLICIES = frozenset(
    {
        MYSTERYBOX_ESO_AUTO_PRIMARY_VERSION_POLICY,
        MYSTERYBOX_ESO_MANUAL_VERSION_POLICY,
    }
)
BUILT_IN_KUBERNETES_NAMESPACES = frozenset(
    {"default", "kube-node-lease", "kube-public", "kube-system"}
)
_KUBERNETES_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _payload(payload_or_config: Any) -> dict[str, Any]:
    if isinstance(payload_or_config, dict):
        return payload_or_config
    plain = to_plain_data(payload_or_config)
    return plain if isinstance(plain, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _as_bool(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else False


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = _as_text(item)
        if not token or token in seen:
            continue
        items.append(token)
        seen.add(token)
    return items


def _app_chart_rows(payload: Mapping[str, Any], app_id: str = "") -> list[dict[str, Any]]:
    apps = payload.get("apps")
    if not isinstance(apps, Mapping):
        return []
    charts = apps.get("charts")
    if not isinstance(charts, list):
        return []
    rows: list[dict[str, Any]] = []
    normalized_app_id = normalize_component_token(app_id)
    for row in charts:
        if not isinstance(row, dict):
            continue
        if normalized_app_id and component_type_id(row) != normalized_app_id:
            continue
        rows.append(row)
    return rows


def _ensure_app_charts(payload: dict[str, Any]) -> list[Any]:
    apps = payload.get("apps")
    if not isinstance(apps, dict):
        apps = {}
        payload["apps"] = apps
    charts = apps.get("charts")
    if not isinstance(charts, list):
        charts = []
        apps["charts"] = charts
    return charts


def _target_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    deploy = payload.get("deploy")
    if not isinstance(deploy, Mapping):
        return []
    targets = deploy.get("targets")
    if not isinstance(targets, list):
        return []
    return [dict(row) for row in targets if isinstance(row, Mapping)]


def _target_ref(row: Mapping[str, Any]) -> str:
    return normalize_component_token(row.get(INSTANCE_ID_FIELD))


def _target_mysterybox_config(row: Mapping[str, Any]) -> dict[str, Any]:
    secrets = row.get("secrets")
    if not isinstance(secrets, Mapping):
        return {}
    mysterybox = secrets.get("mysterybox")
    if not isinstance(mysterybox, Mapping):
        return {}
    return dict(mysterybox)


def _mysterybox_backend_enabled(payload: Mapping[str, Any]) -> bool:
    infra = payload.get("infra")
    if not isinstance(infra, Mapping):
        return False
    components = infra.get("components")
    if not isinstance(components, list):
        return False
    return any(
        isinstance(row, Mapping)
        and bool(row.get("enabled", False))
        and component_type_id(row) == MYSTERYBOX_INFRA_COMPONENT_ID
        for row in components
    )


def _mysterybox_secret_eso_version_policy(secret: Mapping[str, Any]) -> str:
    policy = _as_text(secret.get("eso_version_policy")).lower()
    if not policy:
        return MYSTERYBOX_ESO_AUTO_PRIMARY_VERSION_POLICY
    return policy


def _enabled_mysterybox_secret_refs(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, Mapping) else None
    if not isinstance(components, list):
        return []
    refs: list[dict[str, Any]] = []
    for row in components:
        if (
            not isinstance(row, Mapping)
            or not bool(row.get("enabled", False))
            or component_type_id(row) != MYSTERYBOX_INFRA_COMPONENT_ID
        ):
            continue
        instance_id = component_instance_id(row)
        inputs = row.get("inputs")
        secrets = inputs.get("secrets") if isinstance(inputs, Mapping) else None
        if not instance_id or not isinstance(secrets, list):
            continue
        for secret in secrets:
            if not isinstance(secret, Mapping):
                continue
            secret_name = _as_text(secret.get("name"))
            if not secret_name:
                continue
            ref = {
                "mysterybox_instance_id": instance_id,
                "secret_name": secret_name,
            }
            secret_payload = secret.get("payload")
            if isinstance(secret_payload, Mapping):
                ref["payload_keys"] = [
                    _as_text(payload_key)
                    for payload_key in secret_payload
                    if _as_text(payload_key)
                ]
            kubernetes_secret_name = _as_text(secret.get("kubernetes_secret_name"))
            if kubernetes_secret_name:
                ref["kubernetes_secret_name"] = kubernetes_secret_name
            ref["eso_version_policy"] = _mysterybox_secret_eso_version_policy(secret)
            version_id = _mysterybox_secret_version_id(secret)
            if version_id:
                ref["version"] = version_id
            refs.append(ref)
    return refs


def _enabled_mysterybox_secret_names_by_instance(
    payload: Mapping[str, Any],
) -> dict[str, set[str]]:
    names_by_instance: dict[str, set[str]] = {}
    for ref in _enabled_mysterybox_secret_refs(payload):
        names_by_instance.setdefault(ref["mysterybox_instance_id"], set()).add(
            ref["secret_name"]
        )
    return names_by_instance


def _kubernetes_name(value: str, *, fallback: str = "mysterybox-secret") -> str:
    token = normalize_component_token(value)
    token = re.sub(r"[^a-z0-9-]+", "-", token)
    token = re.sub(r"-+", "-", token).strip("-")
    if not token:
        token = fallback
    if len(token) > 63:
        token = token[:63].strip("-") or fallback
    return token


def _unique_kubernetes_name(value: str, *, seen: set[str]) -> str:
    base = _kubernetes_name(value)
    if base not in seen:
        seen.add(base)
        return base
    for index in range(2, 1000):
        suffix = f"-{index}"
        candidate = f"{base[: 63 - len(suffix)].strip('-')}{suffix}"
        if candidate not in seen:
            seen.add(candidate)
            return candidate
    raise ValueError(f"Could not derive a unique Kubernetes name for MysteryBox secret '{value}'")


def _mysterybox_secret_version_id(secret: Mapping[str, Any]) -> str:
    version_id = _as_text(secret.get("version_id"))
    if not version_id or version_id.lower() == "n/a":
        return ""
    return version_id


def _generated_external_secrets(
    payload: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    target_ref: str = "",
) -> list[dict[str, Any]]:
    refs = _enabled_mysterybox_secret_refs(payload)
    sync_namespaces = _sync_namespaces(config)
    if not sync_namespaces:
        return []
    include_instance_prefix = len({item["mysterybox_instance_id"] for item in refs}) > 1
    external_secrets: list[dict[str, Any]] = []
    if refs:
        for namespace in sync_namespaces:
            seen_names: set[str] = set()
            for ref in refs:
                source_name = (
                    f"{ref['mysterybox_instance_id']}-{ref['secret_name']}"
                    if include_instance_prefix
                    else ref["secret_name"]
                )
                target_secret_name = _kubernetes_name(
                    _as_text(ref.get("kubernetes_secret_name")) or source_name
                )
                name = _unique_kubernetes_name(target_secret_name, seen=seen_names)
                remote_ref_base = {
                    "mysterybox_instance_id": ref["mysterybox_instance_id"],
                    "secret_name": ref["secret_name"],
                }
                version = _as_text(ref.get("version"))
                if (
                    _as_text(ref.get("eso_version_policy"))
                    == MYSTERYBOX_ESO_MANUAL_VERSION_POLICY
                    and version
                ):
                    remote_ref_base["version"] = version
                data = []
                for payload_key in ref.get("payload_keys", []):
                    if not _as_text(payload_key):
                        continue
                    data_item = {
                        **remote_ref_base,
                        "secret_key": _as_text(payload_key),
                        "property": _as_text(payload_key),
                    }
                    data.append(data_item)
                if not data:
                    continue
                external_secrets.append(
                    {
                        "name": name,
                        "namespace": namespace,
                        "target": {"name": name},
                        "data": data,
                    }
                )
    for ref in soperator_notifier_mysterybox_secret_refs(payload, target_ref=target_ref):
        namespace = _as_text(ref.get("namespace"))
        name = _kubernetes_name(_as_text(ref.get("name")))
        secret_id = _as_text(ref.get("secret_id"))
        secret_key = _as_text(ref.get("secret_key"))
        if not namespace or not name or not secret_id or not secret_key:
            continue
        rendered_key = (namespace, _as_text(ref.get("target_name")) or name, secret_key)
        direct_data = {
            "secret_key": secret_key,
            "secret_id": secret_id,
            "property": _as_text(ref.get("property")) or secret_key,
        }
        merged = False
        for item in external_secrets:
            if _as_text(item.get("namespace")) != rendered_key[0]:
                continue
            target = item.get("target")
            target_name = (
                _as_text(target.get("name")) if isinstance(target, Mapping) else ""
            ) or _as_text(item.get("name"))
            if target_name != rendered_key[1]:
                continue
            data = item.get("data")
            if not isinstance(data, list):
                data = []
                item["data"] = data
            for index, data_item in enumerate(data):
                if not isinstance(data_item, Mapping):
                    continue
                if _as_text(data_item.get("secret_key")) == rendered_key[2]:
                    data[index] = copy.deepcopy(direct_data)
                    merged = True
                    break
            if not merged:
                data.append(copy.deepcopy(direct_data))
                merged = True
            break
        if merged:
            continue
        external_secrets.append(
            {
                "name": name,
                "namespace": namespace,
                "target": {"name": _as_text(ref.get("target_name")) or name},
                "data": [copy.deepcopy(direct_data)],
            }
        )
    return external_secrets


def _append_unique(items: list[str], value: str, seen: set[str]) -> None:
    token = normalize_component_token(value)
    if not token or token in seen:
        return
    items.append(token)
    seen.add(token)


def normalize_mysterybox_eso_project_settings(payload_or_config: Any) -> bool:
    payload = _payload(payload_or_config)
    changed = False
    for row in _target_rows(payload):
        target_ref = _target_ref(row)
        if not target_ref or not _as_bool(_target_mysterybox_config(row).get("enabled")):
            continue
        deploy = payload.get("deploy")
        targets = deploy.get("targets") if isinstance(deploy, dict) else None
        if not isinstance(targets, list):
            continue
        target_row = next(
            (
                candidate
                for candidate in targets
                if isinstance(candidate, dict) and _target_ref(candidate) == target_ref
            ),
            None,
        )
        if target_row is None:
            continue
        secrets = target_row.setdefault("secrets", {})
        if not isinstance(secrets, dict):
            secrets = {}
            target_row["secrets"] = secrets
        mysterybox = secrets.setdefault("mysterybox", {})
        if not isinstance(mysterybox, dict):
            mysterybox = {}
            secrets["mysterybox"] = mysterybox
        defaults = {
            "store_name": DEFAULT_STORE_NAME,
            "api_domain": DEFAULT_API_DOMAIN,
            "allow_all_namespaces": True,
            "refresh_interval": DEFAULT_REFRESH_INTERVAL,
        }
        for key, value in defaults.items():
            if key not in mysterybox:
                mysterybox[key] = value
                changed = True
                continue
            if key != "allow_all_namespaces" and not _as_text(mysterybox.get(key)):
                mysterybox[key] = value
                changed = True
        credential_secret = mysterybox.setdefault("credentials_secret", {})
        if not isinstance(credential_secret, dict):
            credential_secret = {}
            mysterybox["credentials_secret"] = credential_secret
            changed = True
        for key, value in {
            "name": DEFAULT_CREDENTIAL_SECRET_NAME,
            "namespace": DEFAULT_CREDENTIAL_SECRET_NAMESPACE,
            "key": DEFAULT_CREDENTIAL_SECRET_KEY,
        }.items():
            if not _as_text(credential_secret.get(key)):
                credential_secret[key] = value
                changed = True
    return changed


def _mysterybox_enabled_targets(payload: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for row in _target_rows(payload):
        target_ref = _target_ref(row)
        if not target_ref:
            continue
        config = _target_mysterybox_config(row)
        if _as_bool(config.get("enabled")):
            rows.append((target_ref, config))
    return rows


def mysterybox_eso_enabled_target_refs(payload_or_config: Any) -> tuple[str, ...]:
    payload = _payload(payload_or_config)
    return tuple(target_ref for target_ref, _config in _mysterybox_enabled_targets(payload))


def mysterybox_eso_app_target_refs(payload_or_config: Any) -> tuple[str, ...]:
    """Return MK8s targets that should get the ESO controller chart."""
    payload = _payload(payload_or_config)
    target_refs: list[str] = []
    seen: set[str] = set()
    for target_ref in mysterybox_eso_enabled_target_refs(payload):
        _append_unique(target_refs, target_ref, seen)
    for ref in soperator_notifier_mysterybox_secret_refs(payload):
        _append_unique(target_refs, _as_text(ref.get("target_ref")), seen)
    if _mysterybox_backend_enabled(payload):
        for target_ref in enabled_cluster_target_refs(payload):
            _append_unique(target_refs, target_ref, seen)
    return tuple(target_refs)


def mysterybox_eso_enabled(payload_or_config: Any, *, target_ref: str | None = None) -> bool:
    target_refs = set(mysterybox_eso_enabled_target_refs(payload_or_config))
    if target_ref is None:
        return bool(target_refs)
    return normalize_component_token(target_ref) in target_refs


def mysterybox_eso_api_domains(
    payload_or_config: Any,
    *,
    target_ref: str | None = None,
) -> tuple[str, ...]:
    payload = _payload(payload_or_config)
    normalized_target_ref = normalize_component_token(target_ref) if target_ref else ""
    domains: list[str] = []
    seen: set[str] = set()
    for current_target_ref, config in _mysterybox_enabled_targets(payload):
        if normalized_target_ref and current_target_ref != normalized_target_ref:
            continue
        api_domain = _as_text(config.get("api_domain")) or DEFAULT_API_DOMAIN
        api_domain = api_domain.removeprefix("https://").removeprefix("http://").rstrip("/")
        if not api_domain or api_domain in seen:
            continue
        domains.append(api_domain)
        seen.add(api_domain)
    return tuple(domains)


def _external_secrets_entry(
    app_entries: tuple[ComponentEntry, ...] | None = None,
) -> ComponentEntry | None:
    entries = app_entries if app_entries is not None else component_entries("apps")
    return next((entry for entry in entries if entry.id == EXTERNAL_SECRETS_APP_ID), None)


def _chart_group(entry: ComponentEntry) -> str:
    group = _as_text(entry.group).lower()
    return group.replace(" ", "-") if group else "platform"


def _chart_repo(entry: ComponentEntry) -> str:
    return _as_text(entry.chart_repo)


def _new_external_secrets_app_row(entry: ComponentEntry, *, target_ref: str) -> dict[str, Any]:
    row = {
        "id": EXTERNAL_SECRETS_APP_ID,
        INSTANCE_ID_FIELD: target_scoped_app_instance_id(
            EXTERNAL_SECRETS_APP_ID, target_ref=target_ref
        ),
        "group": _chart_group(entry),
        "enabled": True,
        "repo": _chart_repo(entry),
        "version": _as_text(entry.version),
        "namespace": _as_text(entry.default_namespace) or "external-secrets",
        "release-name": _as_text(entry.default_release_name) or EXTERNAL_SECRETS_APP_ID,
        "values": {},
    }
    return resolve_component_defaults(
        component_node=row,
        entry=entry,
        preserve_existing_literal=True,
        include_shared=False,
    )


def _apply_external_secrets_defaults(row: dict[str, Any], entry: ComponentEntry) -> None:
    if not _as_text(row.get("repo")):
        row["repo"] = _chart_repo(entry)
    if not _as_text(row.get("version")) and entry.version:
        row["version"] = _as_text(entry.version)
    if not _as_text(row.get("namespace")):
        row["namespace"] = _as_text(entry.default_namespace) or "external-secrets"
    if not _as_text(row.get("release-name")):
        row["release-name"] = _as_text(entry.default_release_name) or EXTERNAL_SECRETS_APP_ID
    if not _as_text(row.get("group")):
        row["group"] = _chart_group(entry)
    if not isinstance(row.get("values"), Mapping):
        row["values"] = {}
    resolved = resolve_component_defaults(
        component_node=row,
        entry=entry,
        preserve_existing_literal=True,
        include_shared=False,
    )
    row.clear()
    row.update(resolved)


def ensure_mysterybox_eso_app_rows(
    payload_or_config: Any,
    *,
    app_entries: tuple[ComponentEntry, ...] | None = None,
) -> bool:
    payload = _payload(payload_or_config)
    enabled_targets = mysterybox_eso_app_target_refs(payload)
    if not enabled_targets:
        return False
    entry = _external_secrets_entry(app_entries)
    if entry is None:
        return False

    charts = _ensure_app_charts(payload)
    changed = False
    rows = _app_chart_rows(payload, EXTERNAL_SECRETS_APP_ID)
    rows_by_target = {
        component_instance_id(row): row
        for row in rows
        if isinstance(row, dict)
    }
    for target_ref in enabled_targets:
        existing = rows_by_target.get(target_ref)
        if existing is None:
            existing = next(
                (
                    row
                    for row in rows
                    if not app_chart_target_ref(row) and not bool(row.get("enabled", False))
                ),
                None,
            )
        if existing is None:
            existing = next((row for row in rows if not app_chart_target_ref(row)), None)
        if existing is None:
            charts.append(_new_external_secrets_app_row(entry, target_ref=target_ref))
            changed = True
            continue
        before = copy.deepcopy(existing)
        existing["enabled"] = True
        existing["id"] = EXTERNAL_SECRETS_APP_ID
        existing.pop("target_ref", None)
        existing[INSTANCE_ID_FIELD] = target_scoped_app_instance_id(
            EXTERNAL_SECRETS_APP_ID,
            target_ref=target_ref,
        )
        _apply_external_secrets_defaults(existing, entry)
        if existing != before:
            changed = True
        rows_by_target[target_ref] = existing
    return changed


def mysterybox_eso_dependency_issues(
    payload_or_config: Any,
    *,
    app_entries: tuple[ComponentEntry, ...] | None = None,
) -> list[str]:
    payload = _payload(payload_or_config)
    enabled_targets = mysterybox_eso_app_target_refs(payload)
    direct_secret_refs = soperator_notifier_mysterybox_secret_refs(payload)
    direct_targets = {
        normalize_component_token(ref.get("target_ref"))
        for ref in direct_secret_refs
        if normalize_component_token(ref.get("target_ref"))
    }
    if not enabled_targets:
        return []
    native_targets = set(mysterybox_eso_enabled_target_refs(payload))
    issues: list[str] = []
    if _external_secrets_entry(app_entries) is None:
        if native_targets:
            issues.append(
                "deploy.targets[].secrets.mysterybox.enabled=true requires bundled apps:external-secrets"
            )
        elif direct_targets:
            issues.append(
                "apps:soperator notifier webhookSource=mysterybox requires bundled "
                "apps:external-secrets for the same target"
            )
        else:
            issues.append(
                "infra.components[id=mysterybox].enabled=true with an MK8s target "
                "requires bundled apps:external-secrets"
            )
        return issues
    rows_by_target = {
        component_instance_id(row): row
        for row in _app_chart_rows(payload, EXTERNAL_SECRETS_APP_ID)
        if isinstance(row, dict) and bool(row.get("enabled", False))
    }
    for target_ref in enabled_targets:
        if target_ref not in rows_by_target:
            if target_ref in native_targets:
                issues.append(
                    f"deploy.targets[instance_id={target_ref}].secrets.mysterybox.enabled=true "
                    "requires apps:external-secrets to be enabled for the same target"
                )
            elif target_ref in direct_targets:
                issues.append(
                    f"apps:soperator notifier webhookSource=mysterybox on target "
                    f"'{target_ref}' requires apps:external-secrets to be enabled for the same target"
                )
            else:
                issues.append(
                    f"infra.components[id=mysterybox].enabled=true with MK8s target "
                    f"'{target_ref}' requires apps:external-secrets to be enabled for the same target"
                )
    if native_targets and not _enabled_mysterybox_secret_refs(payload) and not direct_secret_refs:
        issues.append(
            "deploy.targets[].secrets.mysterybox.enabled=true requires at least one "
            "enabled mysterybox component with inputs.secrets or one bundled app "
            "MysteryBox webhook source"
        )
    for ref in _enabled_mysterybox_secret_refs(payload):
        kubernetes_secret_name = _as_text(ref.get("kubernetes_secret_name"))
        if kubernetes_secret_name and not _KUBERNETES_NAME_PATTERN.fullmatch(
            kubernetes_secret_name
        ):
            issues.append(
                "infra.components[id=mysterybox].inputs.secrets[].kubernetes_secret_name "
                "must be a Kubernetes Secret name"
            )
            break
    return issues


def _credentials_secret(config: Mapping[str, Any]) -> dict[str, str]:
    raw = config.get("credentials_secret")
    mapping = raw if isinstance(raw, Mapping) else {}
    return {
        "name": _as_text(mapping.get("name")) or DEFAULT_CREDENTIAL_SECRET_NAME,
        "namespace": _as_text(mapping.get("namespace")) or DEFAULT_CREDENTIAL_SECRET_NAMESPACE,
        "key": _as_text(mapping.get("key")) or DEFAULT_CREDENTIAL_SECRET_KEY,
    }


def _allow_all_namespaces(config: Mapping[str, Any]) -> bool:
    value = config.get("allow_all_namespaces")
    return value is not False


def _sync_namespaces(config: Mapping[str, Any]) -> list[str]:
    return _list_of_strings(config.get("sync_namespaces"))


def _managed_namespaces(config: Mapping[str, Any]) -> list[str]:
    return _sync_namespaces(config)


def _managed_metadata(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "labels": {
            MYSTERYBOX_ESO_MANAGED_LABEL: MYSTERYBOX_ESO_MANAGED_VALUE,
            MYSTERYBOX_ESO_SOURCE_LABEL: MYSTERYBOX_ESO_SOURCE_VALUE,
        },
    }


def _namespace_doc(name: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": _managed_metadata(name),
    }


def _should_render_namespace_doc(name: str) -> bool:
    return name not in BUILT_IN_KUBERNETES_NAMESPACES


def _cluster_secret_store_doc(config: Mapping[str, Any]) -> dict[str, Any]:
    secret = _credentials_secret(config)
    store_name = _as_text(config.get("store_name")) or DEFAULT_STORE_NAME
    api_domain = _as_text(config.get("api_domain")) or DEFAULT_API_DOMAIN
    spec: dict[str, Any] = {
        "provider": {
            "nebiusmysterybox": {
                "apiDomain": api_domain,
                "auth": {
                    "serviceAccountCredsSecretRef": {
                        "name": secret["name"],
                        "namespace": secret["namespace"],
                        "key": secret["key"],
                    }
                },
            }
        }
    }
    if not _allow_all_namespaces(config):
        spec["conditions"] = [{"namespaces": _sync_namespaces(config)}]
    return {
        "apiVersion": "external-secrets.io/v1",
        "kind": "ClusterSecretStore",
        "metadata": _managed_metadata(store_name),
        "spec": spec,
    }


def _external_secret_target(item: Mapping[str, Any]) -> dict[str, Any]:
    raw = item.get("target")
    target = raw if isinstance(raw, Mapping) else {}
    name = _as_text(target.get("name")) or _as_text(item.get("name"))
    result: dict[str, Any] = {
        "name": name,
        "creationPolicy": _as_text(target.get("creation_policy")) or "Owner",
        "deletionPolicy": _as_text(target.get("deletion_policy")) or "Retain",
        "template": {
            "type": _as_text(target.get("template_type")) or "Opaque",
            "metadata": {
                "labels": {
                    "app.kubernetes.io/managed-by": "external-secrets",
                    MYSTERYBOX_ESO_SOURCE_LABEL: MYSTERYBOX_ESO_SOURCE_VALUE,
                }
            },
        },
    }
    return result


def _single_mysterybox_instance_id(payload: Mapping[str, Any]) -> str:
    instance_ids = {
        item["mysterybox_instance_id"] for item in _enabled_mysterybox_secret_refs(payload)
    }
    return next(iter(instance_ids)) if len(instance_ids) == 1 else ""


def _referenced_mysterybox_instance_id(
    payload: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> str:
    return _as_text(raw.get("mysterybox_instance_id")) or _single_mysterybox_instance_id(payload)


def _resolve_mysterybox_secret_id(
    payload: Mapping[str, Any],
    raw: Mapping[str, Any],
    *,
    component_output_values: Mapping[str, Any],
) -> str:
    secret_id = _as_text(raw.get("secret_id"))
    if secret_id:
        return secret_id
    secret_name = _as_text(raw.get("secret_name"))
    if not secret_name:
        return ""
    instance_id = _referenced_mysterybox_instance_id(payload, raw)
    if not instance_id:
        return ""
    output = component_output_values.get(component_output_ref(instance_id, "secret_ids"))
    if not isinstance(output, Mapping):
        return ""
    return _as_text(output.get(secret_name))


def _external_secret_data(
    payload: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    component_output_values: Mapping[str, Any],
) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = []
    for raw in _list_of_mappings(item.get("data")):
        secret_key = _as_text(raw.get("secret_key"))
        secret_id = _resolve_mysterybox_secret_id(
            payload,
            raw,
            component_output_values=component_output_values,
        )
        if not secret_key or not secret_id:
            continue
        remote_ref: dict[str, Any] = {"key": secret_id}
        property_name = _as_text(raw.get("property"))
        version = _as_text(raw.get("version"))
        if property_name:
            remote_ref["property"] = property_name
        if version:
            remote_ref["version"] = version
        data.append({"secretKey": secret_key, "remoteRef": remote_ref})
    return data


def _external_secret_data_from(
    payload: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    component_output_values: Mapping[str, Any],
) -> list[dict[str, Any]]:
    data_from: list[dict[str, Any]] = []
    for raw in _list_of_mappings(item.get("data_from")):
        secret_id = _resolve_mysterybox_secret_id(
            payload,
            raw,
            component_output_values=component_output_values,
        )
        if not secret_id:
            continue
        extract: dict[str, Any] = {"key": secret_id}
        version = _as_text(raw.get("version"))
        if version:
            extract["version"] = version
        data_from.append({"extract": extract})
    return data_from


def _external_secret_doc(
    payload: Mapping[str, Any],
    config: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    component_output_values: Mapping[str, Any],
) -> dict[str, Any] | None:
    name = _as_text(item.get("name"))
    namespace = _as_text(item.get("namespace"))
    store_name = _as_text(config.get("store_name")) or DEFAULT_STORE_NAME
    spec: dict[str, Any] = {
        "refreshPolicy": _as_text(item.get("refresh_policy")) or "Periodic",
        "refreshInterval": (
            _as_text(item.get("refresh_interval"))
            or _as_text(config.get("refresh_interval"))
            or DEFAULT_REFRESH_INTERVAL
        ),
        "secretStoreRef": {
            "kind": "ClusterSecretStore",
            "name": store_name,
        },
        "target": _external_secret_target(item),
    }
    data = _external_secret_data(payload, item, component_output_values=component_output_values)
    data_from = _external_secret_data_from(
        payload,
        item,
        component_output_values=component_output_values,
    )
    if not data and not data_from:
        return None
    if data:
        spec["data"] = data
    if data_from:
        spec["dataFrom"] = data_from
    metadata = _managed_metadata(name)
    metadata["namespace"] = namespace
    return {
        "apiVersion": "external-secrets.io/v1",
        "kind": "ExternalSecret",
        "metadata": metadata,
        "spec": spec,
    }


def mysterybox_eso_extra_objects_for_target(
    payload_or_config: Any,
    *,
    target_ref: str,
    component_output_values: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    payload = _payload(payload_or_config)
    resolved_component_outputs = component_output_values or {}
    normalized_target_ref = normalize_component_token(target_ref)
    config = next(
        (
            target_config
            for current_target_ref, target_config in _mysterybox_enabled_targets(payload)
            if current_target_ref == normalized_target_ref
        ),
        None,
    )
    if config is None:
        return []
    namespaces = [
        namespace
        for namespace in _managed_namespaces(config)
        if _should_render_namespace_doc(namespace)
    ]
    objects = [_namespace_doc(namespace) for namespace in namespaces]
    objects.append(_cluster_secret_store_doc(config))
    for item in _generated_external_secrets(payload, config, target_ref=normalized_target_ref):
        if _as_text(item.get("name")) and _as_text(item.get("namespace")):
            external_secret = _external_secret_doc(
                payload,
                config,
                item,
                component_output_values=resolved_component_outputs,
            )
            if external_secret is not None:
                objects.append(external_secret)
    return objects


def _is_managed_extra_object(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    metadata = value.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    labels = metadata.get("labels")
    if not isinstance(labels, Mapping):
        return False
    return labels.get(MYSTERYBOX_ESO_MANAGED_LABEL) == MYSTERYBOX_ESO_MANAGED_VALUE


def strip_mysterybox_eso_app_values(payload_or_config: Any) -> bool:
    """Remove cxcli-managed ESO objects from source config app values."""
    payload = _payload(payload_or_config)
    changed = False
    for row in _app_chart_rows(payload, EXTERNAL_SECRETS_APP_ID):
        values = row.get("values")
        if not isinstance(values, dict):
            continue
        existing = values.get("extraObjects")
        if not isinstance(existing, list):
            continue
        preserved = [
            copy.deepcopy(item) for item in existing if not _is_managed_extra_object(item)
        ]
        if preserved == existing:
            continue
        if preserved:
            values["extraObjects"] = preserved
        else:
            values.pop("extraObjects", None)
        changed = True
    return changed


def materialize_mysterybox_eso_app_values(
    payload_or_config: Any,
    *,
    component_output_values: Mapping[str, Any] | None = None,
) -> bool:
    payload = _payload(payload_or_config)
    changed = False
    for row in _app_chart_rows(payload, EXTERNAL_SECRETS_APP_ID):
        if not bool(row.get("enabled", False)):
            continue
        values = row.get("values")
        if not isinstance(values, dict):
            values = {}
            row["values"] = values
        existing = values.get("extraObjects")
        existing_items = existing if isinstance(existing, list) else []
        preserved = [
            copy.deepcopy(item) for item in existing_items if not _is_managed_extra_object(item)
        ]
        before = copy.deepcopy(values.get("extraObjects"))
        if preserved:
            values["extraObjects"] = preserved
        else:
            values.pop("extraObjects", None)
        if values.get("extraObjects") != before:
            changed = True
    return changed


def mysterybox_eso_runtime_secret_specs(
    payload_or_config: Any,
    *,
    target_ref: str | None = None,
) -> tuple[dict[str, str], ...]:
    payload = _payload(payload_or_config)
    normalized_target_ref = normalize_component_token(target_ref) if target_ref else ""
    specs: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for current_target_ref, config in _mysterybox_enabled_targets(payload):
        if normalized_target_ref and current_target_ref != normalized_target_ref:
            continue
        secret = _credentials_secret(config)
        key = (secret["namespace"], secret["name"], secret["key"])
        if key in seen:
            continue
        specs.append(secret)
        seen.add(key)
    return tuple(specs)


def _target_scoped_validation_name(name: str, *, target_ref: str) -> str:
    normalized_target_ref = normalize_component_token(target_ref)
    if not normalized_target_ref:
        return name
    return f"{name} ({normalized_target_ref})"


def _target_scoped_report_file(report_file: str, *, target_ref: str) -> str:
    normalized_target_ref = normalize_component_token(target_ref)
    if not normalized_target_ref:
        return report_file
    stem, suffix = (
        report_file.rsplit(".", maxsplit=1) if "." in report_file else (report_file, "json")
    )
    return f"{stem}-{normalized_target_ref}.{suffix}"


def _external_secret_refs(
    payload: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    target_ref: str,
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for item in _generated_external_secrets(payload, config, target_ref=target_ref):
        name = _as_text(item.get("name"))
        namespace = _as_text(item.get("namespace"))
        if name and namespace:
            refs.append({"namespace": namespace, "name": name})
    return refs


def mysterybox_eso_manages_kubernetes_secret(
    payload_or_config: Any,
    *,
    target_ref: str | None,
    namespace: str,
    name: str,
    key: str,
    component_output_values: Mapping[str, Any] | None = None,
) -> bool:
    """Return true when rendered MysteryBox ESO sync owns a Secret data key."""
    payload = _payload(payload_or_config)
    resolved_component_outputs = component_output_values or {}
    normalized_target_ref = normalize_component_token(target_ref) if target_ref else ""
    expected_namespace = _as_text(namespace)
    expected_name = _as_text(name)
    expected_key = _as_text(key)
    if not expected_namespace or not expected_name or not expected_key:
        return False

    for current_target_ref, config in _mysterybox_enabled_targets(payload):
        if normalized_target_ref and current_target_ref != normalized_target_ref:
            continue
        for item in _generated_external_secrets(payload, config, target_ref=current_target_ref):
            external_secret = _external_secret_doc(
                payload,
                config,
                item,
                component_output_values=resolved_component_outputs,
            )
            if external_secret is None:
                continue
            metadata = external_secret.get("metadata")
            if not isinstance(metadata, Mapping):
                continue
            if _as_text(metadata.get("namespace")) != expected_namespace:
                continue
            spec = external_secret.get("spec")
            if not isinstance(spec, Mapping):
                continue
            target = spec.get("target")
            target_name = _as_text(target.get("name")) if isinstance(target, Mapping) else ""
            if target_name != expected_name:
                continue
            for data_item in _list_of_mappings(spec.get("data")):
                if _as_text(data_item.get("secretKey")) == expected_key:
                    return True
    return False


def _secret_name_ref_instance_ids(
    payload: Mapping[str, Any],
    _config: Mapping[str, Any],
) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for ref in _enabled_mysterybox_secret_refs(payload):
        instance_id = _as_text(ref.get("mysterybox_instance_id"))
        if not instance_id or instance_id in seen:
            continue
        refs.append(instance_id)
        seen.add(instance_id)
    return refs


def mysterybox_eso_terraform_output_specs(payload_or_config: Any) -> tuple[dict[str, str], ...]:
    payload = _payload(payload_or_config)
    specs: list[dict[str, str]] = []
    seen: set[str] = set()
    for _target_ref, config in _mysterybox_enabled_targets(payload):
        for instance_id in _secret_name_ref_instance_ids(payload, config):
            if instance_id in seen:
                continue
            specs.append(
                {
                    "component_id": MYSTERYBOX_INFRA_COMPONENT_ID,
                    "instance_id": instance_id,
                    "output_name": "secret_ids",
                    "source_ref": component_output_ref(instance_id, "secret_ids"),
                }
            )
            seen.add(instance_id)
    return tuple(specs)


def _external_secrets_controller_namespace(payload: Mapping[str, Any], *, target_ref: str) -> str:
    normalized_target_ref = normalize_component_token(target_ref)
    for row in _app_chart_rows(payload, EXTERNAL_SECRETS_APP_ID):
        row_target_ref = app_chart_target_ref(row) or component_instance_id(row)
        if normalize_component_token(row_target_ref) != normalized_target_ref:
            continue
        namespace = _as_text(row.get("namespace"))
        if namespace:
            return namespace
    return DEFAULT_CREDENTIAL_SECRET_NAMESPACE


def mysterybox_eso_validation_specs(payload_or_config: Any) -> list[dict[str, Any]]:
    payload = _payload(payload_or_config)
    specs: list[dict[str, Any]] = []
    for target_ref, config in _mysterybox_enabled_targets(payload):
        external_secret_refs = _external_secret_refs(payload, config, target_ref=target_ref)
        if not external_secret_refs:
            continue
        secret = _credentials_secret(config)
        api_domain = (
            (_as_text(config.get("api_domain")) or DEFAULT_API_DOMAIN)
            .removeprefix("https://")
            .removeprefix("http://")
            .rstrip("/")
        )
        specs.append(
            {
                "kind": MYSTERYBOX_ESO_CONNECTIVITY_VALIDATION_KIND,
                "name": _target_scoped_validation_name(
                    "ESO MysteryBox connectivity",
                    target_ref=target_ref,
                ),
                TARGET_REF_FIELD: target_ref,
                "required": True,
                "store_name": _as_text(config.get("store_name")) or DEFAULT_STORE_NAME,
                "api_domain": api_domain,
                "credentials_secret": dict(secret),
                "eso_namespace": _external_secrets_controller_namespace(
                    payload,
                    target_ref=target_ref,
                ),
                "external_secrets": external_secret_refs,
                "report_file": _target_scoped_report_file(
                    "mysterybox-eso-connectivity-report.json",
                    target_ref=target_ref,
                ),
            }
        )
    return specs
