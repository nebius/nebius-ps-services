"""Canonical read-only Soperator discovery bundle artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .component_instances import normalize_component_token
from .runtime_config import to_plain_data

SOPERATOR_DISCOVERY_SCHEMA = "nebius-cxcli-soperator-discovery/v1"
SOPERATOR_DISCOVERY_DIR_NAME = "soperator-discovery"
SOPERATOR_DISCOVERY_MANIFEST_NAME = "manifest.json"
SOPERATOR_DISCOVERY_SECTION_FILES = (
    "identity.json",
    "kubernetes.json",
    "slurm.json",
    "accounting.json",
    "customizations.json",
    "fingerprints.json",
    "findings.json",
    "summary.md",
)

_SECRET_KEY_PARTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "privatekey",
    "private_key",
    "clientkey",
    "client_key",
    "tls.key",
    "ca.crt",
    "certificate",
    "cert",
)


def _now_z() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(to_plain_data(value), sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sequence_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _normalized_token(value: Any, default: str = "") -> str:
    return normalize_component_token(value) or default


def _redacted_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    compact = normalized.replace("_", "")
    return any(part in normalized or part in compact for part in _SECRET_KEY_PARTS)


def _redact(value: Any, *, parent_key: str = "", redaction: str = "support") -> Any:
    plain = to_plain_data(value)
    if parent_key and _redacted_key(parent_key):
        if plain in ("", None):
            return plain
        return "[redacted]"
    if isinstance(plain, Mapping):
        result: dict[str, Any] = {}
        for key, item in plain.items():
            key_text = str(key)
            result[key_text] = _redact(item, parent_key=key_text, redaction=redaction)
        return result
    if isinstance(plain, list):
        return [_redact(item, parent_key=parent_key, redaction=redaction) for item in plain]
    if isinstance(plain, tuple):
        return [_redact(item, parent_key=parent_key, redaction=redaction) for item in plain]
    return plain


def _sanitize_kubernetes_snapshot(snapshot: Mapping[str, Any], *, redaction: str) -> dict[str, Any]:
    sanitized = _redact(snapshot, redaction=redaction)
    if not isinstance(sanitized, dict):
        return {}
    namespace_resources = sanitized.get("namespace_resources")
    if isinstance(namespace_resources, dict):
        for namespace_payload in namespace_resources.values():
            if not isinstance(namespace_payload, dict):
                continue
            secrets = namespace_payload.get("secrets")
            if isinstance(secrets, list):
                for secret in secrets:
                    if isinstance(secret, dict):
                        secret.pop("data", None)
                        secret.pop("stringData", None)
                        secret.setdefault("data_redacted", True)
    return sanitized


def _release_chart_version(release: Mapping[str, Any]) -> str:
    explicit = str(release.get("chart_version", "") or "").strip()
    if explicit:
        return explicit
    chart = str(release.get("chart", "") or "").strip()
    if "-" not in chart:
        return ""
    return chart.rsplit("-", 1)[-1]


def _soperator_release(snapshot: Mapping[str, Any], *, namespace: str = "", release_name: str = "") -> Mapping[str, Any]:
    releases = _sequence_of_mappings(snapshot.get("helm_releases"))
    requested_namespace = str(namespace or "").strip().lower()
    requested_release = str(release_name or "").strip().lower()
    for release in releases:
        name = str(release.get("name", "") or "").strip().lower()
        release_namespace = str(release.get("namespace", "") or "").strip().lower()
        if requested_release and name != requested_release:
            continue
        if requested_namespace and release_namespace != requested_namespace:
            continue
        if name in {"soperator", "slurm-operator", "soperator-controller"}:
            return release
    for release in releases:
        chart = str(release.get("chart", "") or release.get("chart_name", "") or "").lower()
        name = str(release.get("name", "") or "").strip().lower()
        if "soperator" in chart or "slurm-operator" in chart or name in {"soperator", "slurm-operator"}:
            return release
    return {}


def _resource_items(snapshot: Mapping[str, Any], resource_key: str) -> list[Mapping[str, Any]]:
    resources = snapshot.get("soperator_resources")
    if not isinstance(resources, Mapping):
        return []
    raw = resources.get(resource_key)
    if isinstance(raw, Mapping):
        items = raw.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, Mapping)]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, Mapping)]
    return []


def _object_name(item: Mapping[str, Any]) -> str:
    metadata = item.get("metadata")
    if isinstance(metadata, Mapping):
        return str(metadata.get("name", "") or "").strip()
    return ""


def _identity_section(
    *,
    target_ref: str,
    source_kind: str,
    snapshot: Mapping[str, Any],
    report: Mapping[str, Any],
    cluster_id: str,
    cluster_name: str,
    namespace: str,
    release_name: str,
    kube_context: str,
) -> dict[str, Any]:
    release = _soperator_release(snapshot, namespace=namespace, release_name=release_name)
    detected_namespace = str(release.get("namespace", "") or namespace or "").strip()
    detected_release_name = str(release.get("name", "") or release_name or "").strip()
    detected_chart_version = _release_chart_version(release)
    detected_app_version = str(
        release.get("app_version", "") or release.get("appVersion", "") or ""
    ).strip()
    return {
        "target_ref": _normalized_token(target_ref, "mk8s"),
        "source_kind": source_kind,
        "cluster_id": str(cluster_id or "").strip(),
        "cluster_name": str(cluster_name or "").strip(),
        "kube_context": str(kube_context or "").strip(),
        "namespace": detected_namespace,
        "release_name": detected_release_name,
        "chart_version": detected_chart_version,
        "app_version": detected_app_version,
        "source_version": str(report.get("source_version", "") or "").strip(),
        "target_version": str(report.get("target_version", "") or "").strip(),
        "state": str(report.get("state", "") or "").strip(),
        "fingerprint": str(report.get("fingerprint", "") or "").strip(),
        "helm_release": _redact(release),
        "crd_versions": _redact(snapshot.get("crds", [])),
    }


def _kubernetes_section(snapshot: Mapping[str, Any], *, redaction: str) -> dict[str, Any]:
    sanitized_snapshot = _sanitize_kubernetes_snapshot(snapshot, redaction=redaction)
    namespace_resources = sanitized_snapshot.get("namespace_resources")
    resource_counts: dict[str, Any] = {}
    if isinstance(namespace_resources, Mapping):
        for namespace, resources in namespace_resources.items():
            if not isinstance(resources, Mapping):
                continue
            resource_counts[str(namespace)] = {
                str(key): len(value) if isinstance(value, list) else 0
                for key, value in resources.items()
            }
    return {
        "snapshot": sanitized_snapshot,
        "resource_counts": resource_counts,
        "nodesets": [_redact(item, redaction=redaction) for item in _resource_items(snapshot, "nodesets")],
        "slurmclusters": [
            _redact(item, redaction=redaction) for item in _resource_items(snapshot, "slurmclusters")
        ],
        "activechecks": [
            _redact(item, redaction=redaction) for item in _resource_items(snapshot, "activechecks")
        ],
        "nodeconfigurators": [
            _redact(item, redaction=redaction)
            for item in _resource_items(snapshot, "nodeconfigurators")
        ],
    }


def _slurm_section(slurm_snapshot: Mapping[str, Any] | None, *, redaction: str) -> dict[str, Any]:
    if not isinstance(slurm_snapshot, Mapping):
        return {
            "available": False,
            "collection_errors": [
                {
                    "severity": "recommended",
                    "message": "Slurm runtime discovery was not collected.",
                }
            ],
        }
    return _redact(slurm_snapshot, redaction=redaction)


def _accounting_section(
    accounting_snapshot: Mapping[str, Any] | None,
    *,
    chart_values: Mapping[str, Any] | None,
    redaction: str,
) -> dict[str, Any]:
    values = chart_values if isinstance(chart_values, Mapping) else {}
    external_db = _nested_bool(values, ("slurmNodes", "accounting", "externalDB", "enabled"))
    managed_mariadb = _nested_bool(values, ("slurmNodes", "accounting", "mariadbOperator", "enabled"))
    section: dict[str, Any] = {
        "external_db_enabled": external_db,
        "chart_managed_mariadb": bool(managed_mariadb and not external_db),
        "db_dump_included": False,
        "redaction": redaction,
    }
    if isinstance(accounting_snapshot, Mapping):
        section.update(_redact(accounting_snapshot, redaction=redaction))
    else:
        section["collection_errors"] = [
            {
                "severity": "recommended",
                "message": "Slurm accounting discovery was not collected.",
            }
        ]
    return section


def _nested_bool(mapping: Mapping[str, Any], path: Sequence[str]) -> bool:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping):
            return False
        current = current.get(key)
    return current is True or str(current).strip().lower() == "true"


def _customizations_section(
    *,
    snapshot: Mapping[str, Any],
    chart_values: Mapping[str, Any] | None,
    redaction: str,
) -> dict[str, Any]:
    values = chart_values if isinstance(chart_values, Mapping) else {}
    slurmclusters = _resource_items(snapshot, "slurmclusters")
    nodesets = _resource_items(snapshot, "nodesets")
    return {
        "chart_values": _redact(values, redaction=redaction),
        "slurmclusters": [
            {
                "name": _object_name(item),
                "spec": _redact(item.get("spec", {}), redaction=redaction),
            }
            for item in slurmclusters
        ],
        "nodesets": [
            {
                "name": _object_name(item),
                "spec": _redact(item.get("spec", {}), redaction=redaction),
            }
            for item in nodesets
        ],
        "node_groups": _redact(snapshot.get("node_groups", {}), redaction=redaction),
        "storage": _redact(snapshot.get("storage", {}), redaction=redaction),
        "configmaps": _configmap_refs(snapshot),
        "secret_references": _secret_refs(snapshot),
    }


def _configmap_refs(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    namespace_resources = snapshot.get("namespace_resources")
    if not isinstance(namespace_resources, Mapping):
        return refs
    for namespace, resources in namespace_resources.items():
        if not isinstance(resources, Mapping):
            continue
        for item in resources.get("configmaps", []) if isinstance(resources.get("configmaps"), list) else []:
            if not isinstance(item, Mapping):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
            refs.append(
                {
                    "namespace": str(namespace),
                    "name": str(metadata.get("name", "") or "").strip(),
                    "data_keys": sorted(str(key) for key in (item.get("data_keys") or [])),
                }
            )
    return refs


def _secret_refs(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    namespace_resources = snapshot.get("namespace_resources")
    if not isinstance(namespace_resources, Mapping):
        return refs
    for namespace, resources in namespace_resources.items():
        if not isinstance(resources, Mapping):
            continue
        for item in resources.get("secrets", []) if isinstance(resources.get("secrets"), list) else []:
            if not isinstance(item, Mapping):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
            refs.append(
                {
                    "namespace": str(namespace),
                    "name": str(metadata.get("name", "") or "").strip(),
                    "type": str(item.get("type", "") or "").strip(),
                    "data_keys": sorted(str(key) for key in (item.get("data_keys") or [])),
                    "data_redacted": True,
                }
            )
    return refs


def _fingerprints_section(
    *,
    report: Mapping[str, Any],
    kubernetes: Mapping[str, Any],
    slurm: Mapping[str, Any],
    accounting: Mapping[str, Any],
    customizations: Mapping[str, Any],
) -> dict[str, Any]:
    protected = {
        "slurm": slurm,
        "accounting": accounting,
        "customizations": customizations,
        "nodesets": kubernetes.get("nodesets", []),
        "slurmclusters": kubernetes.get("slurmclusters", []),
    }
    return {
        "onboarding_fingerprint": str(report.get("fingerprint", "") or "").strip(),
        "protected_config_hash": _sha256_text(_stable_json(protected)),
        "slurm_hash": _sha256_text(_stable_json(slurm)),
        "accounting_hash": _sha256_text(_stable_json(accounting)),
        "customizations_hash": _sha256_text(_stable_json(customizations)),
        "kubernetes_policy_hash": _sha256_text(
            _stable_json(
                {
                    "nodesets": kubernetes.get("nodesets", []),
                    "slurmclusters": kubernetes.get("slurmclusters", []),
                    "activechecks": kubernetes.get("activechecks", []),
                    "nodeconfigurators": kubernetes.get("nodeconfigurators", []),
                }
            )
        ),
    }


def _finding_classification(severity: str) -> str:
    normalized = str(severity or "").strip().lower()
    if normalized in {"blocking", "blocker", "fatal"}:
        return "blocking"
    if normalized in {"required", "error", "failed", "fail"}:
        return "required"
    if normalized in {"recommended", "warning", "warn"}:
        return "recommended"
    return "info"


def _findings_section(report: Mapping[str, Any], *, slurm: Mapping[str, Any], accounting: Mapping[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for item in report.get("findings", []) if isinstance(report.get("findings"), list) else []:
        if not isinstance(item, Mapping):
            continue
        severity = str(item.get("severity", "") or "").strip()
        row = dict(item)
        row["classification"] = _finding_classification(severity)
        row["requires_customer_approval"] = row["classification"] in {"required", "blocking"}
        findings.append(row)
    for source, section in (("slurm", slurm), ("accounting", accounting)):
        errors = section.get("collection_errors") if isinstance(section, Mapping) else None
        if not isinstance(errors, list):
            continue
        for error in errors:
            if not isinstance(error, Mapping):
                continue
            severity = str(error.get("severity", "") or "recommended")
            findings.append(
                {
                    "layer": source,
                    "status": "partial-discovery",
                    "severity": severity,
                    "classification": _finding_classification(severity),
                    "message": str(error.get("message", "") or "Optional discovery collector failed."),
                    "requires_customer_approval": False,
                    "evidence": _redact(error),
                }
            )
    actions: list[dict[str, Any]] = []
    for item in report.get("actions", []) if isinstance(report.get("actions"), list) else []:
        if not isinstance(item, Mapping):
            continue
        action = dict(item)
        action["requires_customer_approval"] = bool(
            action.get("required") or action.get("selected") or action.get("disruptive")
        )
        actions.append(action)
    remediation = [
        dict(item)
        for item in report.get("remediation", [])
        if isinstance(item, Mapping)
    ]
    return {
        "onboarding_report": copy.deepcopy(dict(report)),
        "findings": findings,
        "actions": actions,
        "remediation": remediation,
        "blocking_count": sum(1 for item in findings if item.get("classification") == "blocking"),
        "required_count": sum(1 for item in findings if item.get("classification") == "required"),
        "recommended_count": sum(
            1 for item in findings if item.get("classification") == "recommended"
        ),
    }


def _summary_markdown(
    *,
    identity: Mapping[str, Any],
    findings: Mapping[str, Any],
    bundle_dir: Path,
) -> str:
    target_ref = str(identity.get("target_ref", "") or "").strip()
    state = str(identity.get("state", "") or "").strip() or "unknown"
    source_version = str(identity.get("source_version", "") or "").strip() or "unknown"
    target_version = str(identity.get("target_version", "") or "").strip() or "unknown"
    namespace = str(identity.get("namespace", "") or "").strip() or "unknown"
    release_name = str(identity.get("release_name", "") or "").strip() or "unknown"
    lines = [
        "# Soperator Discovery Summary",
        "",
        f"- Target: `{target_ref}`",
        f"- Bundle: `{bundle_dir}`",
        f"- State: `{state}`",
        f"- Namespace: `{namespace}`",
        f"- Release: `{release_name}`",
        f"- Source version: `{source_version}`",
        f"- Target version: `{target_version}`",
        f"- Blocking findings: `{findings.get('blocking_count', 0)}`",
        f"- Required findings: `{findings.get('required_count', 0)}`",
        f"- Recommended findings: `{findings.get('recommended_count', 0)}`",
        "",
        "Discovery is not a backup. Raw Secret values, DB dumps, SQL, tokens, and cert material are not included.",
        "",
    ]
    return "\n".join(lines)


def _write_text_if_changed(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with suppress(OSError):
            if path.read_text(encoding="utf-8") == text:
                return
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(text)
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            with suppress(OSError):
                tmp_path.unlink()


def _write_json_if_changed(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text_if_changed(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _stable_findings_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    stable = copy.deepcopy(to_plain_data(payload))
    report = stable.get("onboarding_report") if isinstance(stable, dict) else None
    if isinstance(report, dict):
        report.pop("analyzed_at", None)
    return stable if isinstance(stable, dict) else {}


def _preserve_stable_report_timestamp(path: Path, payload: dict[str, Any]) -> None:
    if not path.exists():
        return
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(existing, Mapping):
        return
    if _stable_findings_payload(existing) != _stable_findings_payload(payload):
        return
    existing_report = existing.get("onboarding_report")
    report = payload.get("onboarding_report")
    if not isinstance(existing_report, Mapping) or not isinstance(report, dict):
        return
    analyzed_at = str(existing_report.get("analyzed_at", "") or "").strip()
    if analyzed_at:
        report["analyzed_at"] = analyzed_at


def _stable_manifest_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    stable = copy.deepcopy(to_plain_data(payload))
    if isinstance(stable, dict):
        stable.pop("generated_at", None)
    return stable if isinstance(stable, dict) else {}


def _preserve_stable_manifest_timestamp(path: Path, payload: dict[str, Any]) -> None:
    if not path.exists():
        return
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(existing, Mapping):
        return
    if _stable_manifest_payload(existing) != _stable_manifest_payload(payload):
        return
    generated_at = str(existing.get("generated_at", "") or "").strip()
    if generated_at:
        payload["generated_at"] = generated_at


def soperator_discovery_bundle_dir(
    project_dir: Path,
    target_ref: str,
    *,
    output_dir: Path | None = None,
) -> Path:
    if output_dir is not None:
        return output_dir
    normalized = _normalized_token(target_ref, "mk8s")
    return project_dir / "generated" / "reports" / SOPERATOR_DISCOVERY_DIR_NAME / normalized


def soperator_discovery_manifest_path(
    project_dir: Path,
    target_ref: str,
    *,
    output_dir: Path | None = None,
) -> Path:
    return soperator_discovery_bundle_dir(
        project_dir,
        target_ref,
        output_dir=output_dir,
    ) / SOPERATOR_DISCOVERY_MANIFEST_NAME


def write_soperator_discovery_bundle(
    project_dir: Path,
    *,
    target_ref: str,
    snapshot: Mapping[str, Any],
    report: Any,
    source_kind: str,
    command: Sequence[str] | None = None,
    cluster_id: str = "",
    cluster_name: str = "",
    namespace: str = "",
    release_name: str = "",
    kube_context: str = "",
    chart_values: Mapping[str, Any] | None = None,
    slurm_snapshot: Mapping[str, Any] | None = None,
    accounting_snapshot: Mapping[str, Any] | None = None,
    target_versions: Mapping[str, Any] | None = None,
    output_dir: Path | None = None,
    redaction: str = "support",
) -> Path:
    normalized_target = _normalized_token(target_ref, "mk8s")
    bundle_dir = soperator_discovery_bundle_dir(
        project_dir,
        normalized_target,
        output_dir=output_dir,
    )
    bundle_dir.mkdir(parents=True, exist_ok=True)
    report_payload = report.to_dict() if hasattr(report, "to_dict") else dict(report)
    plain_snapshot = to_plain_data(snapshot)
    if not isinstance(plain_snapshot, Mapping):
        plain_snapshot = {}
    identity = _identity_section(
        target_ref=normalized_target,
        source_kind=source_kind,
        snapshot=plain_snapshot,
        report=report_payload,
        cluster_id=cluster_id,
        cluster_name=cluster_name,
        namespace=namespace,
        release_name=release_name,
        kube_context=kube_context,
    )
    kubernetes = _kubernetes_section(plain_snapshot, redaction=redaction)
    slurm = _slurm_section(slurm_snapshot, redaction=redaction)
    accounting = _accounting_section(
        accounting_snapshot,
        chart_values=chart_values,
        redaction=redaction,
    )
    customizations = _customizations_section(
        snapshot=plain_snapshot,
        chart_values=chart_values,
        redaction=redaction,
    )
    fingerprints = _fingerprints_section(
        report=report_payload,
        kubernetes=kubernetes,
        slurm=slurm,
        accounting=accounting,
        customizations=customizations,
    )
    findings = _findings_section(report_payload, slurm=slurm, accounting=accounting)
    sections: dict[str, Any] = {
        "identity.json": identity,
        "kubernetes.json": kubernetes,
        "slurm.json": slurm,
        "accounting.json": accounting,
        "customizations.json": customizations,
        "fingerprints.json": fingerprints,
        "findings.json": findings,
    }
    findings_path = bundle_dir / "findings.json"
    _preserve_stable_report_timestamp(findings_path, findings)
    for filename, payload in sections.items():
        _write_json_if_changed(bundle_dir / filename, payload)
    summary_text = _summary_markdown(
        identity=identity,
        findings=findings,
        bundle_dir=bundle_dir,
    )
    _write_text_if_changed(bundle_dir / "summary.md", summary_text)
    checksums = {
        filename: _sha256_file(bundle_dir / filename)
        for filename in SOPERATOR_DISCOVERY_SECTION_FILES
        if (bundle_dir / filename).is_file()
    }
    manifest: dict[str, Any] = {
        "schema": SOPERATOR_DISCOVERY_SCHEMA,
        "generated_at": _now_z(),
        "target_ref": normalized_target,
        "source_kind": source_kind,
        "command": [str(item) for item in command or ()],
        "redaction": redaction,
        "bundle_dir": str(bundle_dir),
        "sections": {filename: filename for filename in SOPERATOR_DISCOVERY_SECTION_FILES},
        "checksums": checksums,
        "cxcli_version": "",
        "target_versions": _redact(target_versions or {}, redaction=redaction),
    }
    manifest_path = bundle_dir / SOPERATOR_DISCOVERY_MANIFEST_NAME
    _preserve_stable_manifest_timestamp(manifest_path, manifest)
    _write_json_if_changed(manifest_path, manifest)
    return manifest_path


def load_soperator_discovery_bundle(path: Path) -> dict[str, Any]:
    manifest_path = path / SOPERATOR_DISCOVERY_MANIFEST_NAME if path.is_dir() else path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise ValueError(f"Soperator discovery manifest is not a JSON object: {manifest_path}")
    if manifest.get("schema") != SOPERATOR_DISCOVERY_SCHEMA:
        raise ValueError(
            f"Unsupported Soperator discovery schema in {manifest_path}: {manifest.get('schema')}"
        )
    bundle_dir = manifest_path.parent
    checksums = manifest.get("checksums")
    if isinstance(checksums, Mapping):
        for filename, expected in checksums.items():
            section_path = bundle_dir / str(filename)
            if not section_path.is_file():
                raise ValueError(f"Soperator discovery section is missing: {section_path}")
            actual = _sha256_file(section_path)
            if str(expected) != actual:
                raise ValueError(f"Soperator discovery checksum mismatch for {section_path}")
    sections: dict[str, Any] = {}
    for filename in SOPERATOR_DISCOVERY_SECTION_FILES:
        section_path = bundle_dir / filename
        if not section_path.is_file():
            continue
        if filename.endswith(".json"):
            sections[filename] = json.loads(section_path.read_text(encoding="utf-8"))
        else:
            sections[filename] = section_path.read_text(encoding="utf-8")
    findings = sections.get("findings.json")
    kubernetes = sections.get("kubernetes.json")
    identity = sections.get("identity.json")
    report = findings.get("onboarding_report") if isinstance(findings, Mapping) else None
    snapshot = kubernetes.get("snapshot") if isinstance(kubernetes, Mapping) else None
    payload = copy.deepcopy(dict(manifest))
    if isinstance(identity, Mapping):
        for key in (
            "cluster_id",
            "cluster_name",
            "kube_context",
            "namespace",
            "release_name",
            "chart_version",
            "app_version",
        ):
            payload[key] = identity.get(key, "")
    payload.update(
        {
            "bundle_dir": str(bundle_dir),
            "manifest_path": str(manifest_path),
            "sections_payload": sections,
            "report": report if isinstance(report, Mapping) else {},
            "snapshot": snapshot if isinstance(snapshot, Mapping) else {},
        }
    )
    return payload
