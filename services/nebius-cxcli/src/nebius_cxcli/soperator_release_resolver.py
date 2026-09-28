"""Freeze dynamic Soperator release discovery into immutable operation authority."""

from __future__ import annotations

import hashlib
import os
import random
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.parse
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from . import kubernetes_process
from .archive_safety import open_bounded_tar_gz
from .oci_image import is_immutable_oci_image_reference, resolve_oci_image
from .soperator_cache import prepare_private_cache_root
from .soperator_release import (
    SOPERATOR_ADAPTER_MOUNT_IMAGE,
    SOPERATOR_UPSTREAM_CHART_ROLES,
    SoperatorArtifactRequest,
    SoperatorReleaseMetadata,
    SoperatorReleaseSnapshot,
    SoperatorSourceChart,
    SoperatorVersion,
    VerifiedSoperatorSource,
    classify_soperator_release_capabilities,
    load_soperator_release_snapshot,
    normalize_soperator_release_selector,
    resolve_soperator_release,
    seal_soperator_release_snapshot,
    soperator_upstream_registry,
    write_soperator_release_snapshot,
)
from .soperator_release_identity import SoperatorReleaseIdentityLedger
from .soperator_release_source import (
    SoperatorSourceReceipt,
    acquire_soperator_release_source,
    default_soperator_source_cache_root,
    ensure_soperator_release_source,
    normalized_tree_manifest,
)
from .soperator_upgrade_progress import sanitized_bounded_command_output

_RECENT_RELEASE_SNAPSHOT_MAX_AGE_SECONDS = 15 * 60

_CHART_KEY_BY_NAME = dict(SOPERATOR_UPSTREAM_CHART_ROLES)
_THIRD_PARTY_KEY_BY_CHART = {
    "raw": "namespaceRaw",
    "cert-manager": "certManager",
    "kruise": "kruise",
    "mariadb-operator-crds": "mariadbOperatorCrds",
    "mariadb-operator": "mariadbOperator",
    "security-profiles-operator": "securityProfilesOperator",
    "k8up": "k8up",
    "opentelemetry-collector": "opentelemetryCollector",
    "prometheus-operator-crds": "prometheusOperatorCrds",
    "victoria-metrics-operator-crds": "victoriaMetricsOperatorCrds",
    "victoria-logs-single": "victoriaLogs",
    "victoria-metrics-k8s-stack": "victoriaMetricsStack",
    "csi-driver-nfs": "csiDriverNfs",
}
_DIGEST_RE = re.compile(r"Digest:\s*(sha256:[0-9a-f]{64})")
_IMAGE_RE = re.compile(
    r"(?<![A-Za-z0-9._/-])"
    r"(?:[A-Za-z0-9.-]+(?::[0-9]+)?/)+[A-Za-z0-9._/-]+"
    r"(?::[A-Za-z0-9._-]+|@sha256:[0-9a-f]{64})"
)
_MAX_CHART_PACKAGE_BYTES = 64 * 1024 * 1024
_MAX_CHART_TAR_BYTES = 80 * 1024 * 1024
_MAX_CHART_PACKAGE_MEMBERS = 10_000
_MAX_CHART_METADATA_BYTES = 1024 * 1024
_CHART_PULL_ATTEMPTS = 3
_CHART_PULL_BASE_BACKOFF_SECONDS = 2.0
_CHART_PULL_MAX_JITTER_SECONDS = 0.5


def _artifact_key(*identity: str) -> str:
    material = "\0".join(identity).encode()
    return "chart-" + hashlib.sha256(material).hexdigest()[:24]


def _notify(emit: Callable[[str], None] | None, message: str) -> None:
    if emit is None:
        return
    try:
        emit(message)
    except Exception:
        # Progress presentation must not change release authority or verification.
        return


@dataclass(frozen=True)
class _ChartPackageLimits:
    max_compressed_bytes: int = _MAX_CHART_PACKAGE_BYTES
    max_members: int = _MAX_CHART_PACKAGE_MEMBERS
    max_member_bytes: int = _MAX_CHART_PACKAGE_BYTES
    max_expanded_bytes: int = _MAX_CHART_PACKAGE_BYTES
    max_metadata_bytes: int = _MAX_CHART_METADATA_BYTES
    max_tar_bytes: int = _MAX_CHART_TAR_BYTES


_DEFAULT_CHART_PACKAGE_LIMITS = _ChartPackageLimits()


@dataclass(frozen=True)
class FrozenSoperatorRelease:
    metadata: SoperatorReleaseMetadata
    source: SoperatorSourceReceipt
    snapshot: SoperatorReleaseSnapshot

    @cached_property
    def source_context(self) -> VerifiedSoperatorSource:
        return describe_soperator_source(
            self.metadata,
            self.source,
            jail_identity=(self.snapshot.populate_jail_image, self.snapshot.jail_cuda_version),
        )


def describe_soperator_source(
    metadata: SoperatorReleaseMetadata,
    source: SoperatorSourceReceipt,
    *,
    jail_identity: tuple[str, str] | None = None,
) -> VerifiedSoperatorSource:
    """Describe verified Git files without acquiring optional chart packages."""
    if (metadata.release, metadata.commit, metadata.tree) != (
        source.release,
        source.commit,
        source.tree,
    ):
        raise ValueError("Soperator source receipt differs from verified release identity")
    root = Path(source.source_dir)
    contract, capability = classify_soperator_release_capabilities(root)
    if contract != "upstream-flux-v1":
        raise ValueError("Soperator source does not implement the supported Flux contract")
    charts = {}
    names = set()
    for path in sorted(root.glob("helm/*/Chart.yaml")):
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, Mapping):
            raise ValueError("Invalid source chart metadata")
        name, version = str(data.get("name") or ""), str(data.get("version") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name) or not version or name in names:
            raise ValueError("Invalid or ambiguous source chart identity")
        names.add(name)
        digest, _ = normalized_tree_manifest(path.parent)
        charts[_CHART_KEY_BY_NAME.get(name) or _artifact_key(name)] = SoperatorSourceChart(
            name, version, path.parent.relative_to(root).as_posix(), digest
        )
    if "umbrella" not in charts:
        raise ValueError("Soperator source has no umbrella")
    registry = soperator_upstream_registry(root)
    image, cuda = jail_identity or _populate_jail_identity(root)
    return VerifiedSoperatorSource(
        metadata, source, registry, charts, contract, capability, image, cuda
    )


def resolve_soperator_source(
    selector: str,
    *,
    cache_root: Path | None = None,
    identity_root: Path | None = None,
    opener: Any = None,
    emit: Callable[[str], None] | None = None,
) -> VerifiedSoperatorSource:
    """Verify source authority for configuration; this grants no package admission."""
    _notify(emit, "Verifying official Soperator source and configuration defaults")
    metadata = resolve_soperator_release(selector, opener=opener)
    ledger = SoperatorReleaseIdentityLedger(identity_root)
    with ledger.locked(metadata) as identity:
        receipt = acquire_soperator_release_source(metadata, cache_root=cache_root, opener=opener)
        described = describe_soperator_source(metadata, receipt)
        fresh = resolve_soperator_release(metadata.release, opener=opener)
        if (fresh.commit, fresh.tree) != (metadata.commit, metadata.tree):
            raise ValueError("official Soperator release tag moved while verifying source")
        ledger.record(identity)
    return described


@dataclass(frozen=True)
class SoperatorObservabilityDefaults:
    metadata: SoperatorReleaseMetadata
    source: SoperatorSourceReceipt
    values: dict[str, Any]


def resolve_soperator_observability_defaults(
    release: str, *, emit: Callable[[str], None] | None = None
) -> SoperatorObservabilityDefaults:
    """Verify source-only query defaults without acquiring the deployment chart graph."""
    from .soperator_values import read_observability_defaults

    _notify(emit, f"Verifying Soperator {release} source for datasource connections...")
    metadata = resolve_soperator_release(release)
    ledger = SoperatorReleaseIdentityLedger()
    _notify(emit, "Checking the verified release identity and acquiring its source defaults...")
    with ledger.locked(metadata) as identity:
        source = acquire_soperator_release_source(metadata)
        if (source.release, source.commit, source.tree) != (
            metadata.release,
            metadata.commit,
            metadata.tree,
        ):
            raise ValueError("Soperator source receipt does not match resolved release metadata")
        root = Path(source.source_dir)
        contract, _ = classify_soperator_release_capabilities(root)
        if contract != "upstream-flux-v1":
            raise ValueError("Soperator datasource defaults require the upstream Flux contract")
        defaults = read_observability_defaults(root / "helm/soperator-fluxcd/values.yaml")
        _notify(emit, "Re-verifying the Soperator release tag identity...")
        fresh = resolve_soperator_release(metadata.release)
        if (fresh.repository, fresh.tag, fresh.commit, fresh.tree) != (
            metadata.repository,
            metadata.tag,
            metadata.commit,
            metadata.tree,
        ):
            raise RuntimeError(
                "official Soperator release tag moved while its defaults were verified"
            )
        ledger.record(identity)
    _notify(emit, "Verified Soperator datasource defaults are ready.")
    return SoperatorObservabilityDefaults(metadata, source, defaults)


_FROZEN_RELEASES: ContextVar[dict[str, FrozenSoperatorRelease] | None] = ContextVar(
    "nebius_cxcli_frozen_soperator_release",
    default=None,
)


def current_frozen_soperator_release(
    release: str,
    *,
    target_ref: str,
    request_sha256: str | None = None,
) -> FrozenSoperatorRelease | None:
    frozen = (_FROZEN_RELEASES.get() or {}).get(target_ref)
    if frozen is None:
        return None
    if frozen.snapshot.release != release:
        raise ValueError("Bound Soperator target has a different release")
    if request_sha256 is not None and frozen.snapshot.request_sha256 != request_sha256:
        raise ValueError("Bound Soperator target has a different admission request")
    return frozen


@contextmanager
def use_frozen_soperator_release(frozen: FrozenSoperatorRelease):
    releases = dict(_FROZEN_RELEASES.get() or {})
    target = frozen.snapshot.target_ref
    previous = releases.get(target)
    if previous is not None and previous.snapshot != frozen.snapshot:
        raise ValueError("Conflicting frozen Soperator snapshots for one target")
    releases[target] = frozen
    token = _FROZEN_RELEASES.set(releases)
    try:
        yield frozen
    finally:
        _FROZEN_RELEASES.reset(token)


def _run(command: Sequence[str], *, label: str) -> subprocess.CompletedProcess[str]:
    timeout = 300
    try:
        result = kubernetes_process.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raw_detail = exc.stderr or exc.stdout or ""
        if isinstance(raw_detail, bytes):
            raw_detail = raw_detail.decode("utf-8", errors="replace")
        detail = sanitized_bounded_command_output(raw_detail)
        raise RuntimeError(
            f"{label} timed out after {timeout} seconds" + (f": {detail}" if detail else "")
        ) from None
    if result.returncode != 0:
        detail = sanitized_bounded_command_output(result.stderr or result.stdout or "")
        raise RuntimeError(
            f"{label} failed with exit code {result.returncode}" + (f": {detail}" if detail else "")
        )
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _chart_metadata_from_package(
    path: Path,
    *,
    limits: _ChartPackageLimits = _DEFAULT_CHART_PACKAGE_LIMITS,
) -> Mapping[str, Any]:
    package_stat = path.lstat()
    if not stat.S_ISREG(package_stat.st_mode) or package_stat.st_nlink != 1:
        raise ValueError("downloaded Helm package must be a regular single-link file")
    if package_stat.st_size > limits.max_compressed_bytes:
        raise ValueError("downloaded Helm package exceeds the size limit")

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise RuntimeError("This platform cannot safely inspect Helm packages")
    flags = os.O_RDONLY | os.O_CLOEXEC | nofollow
    descriptor = os.open(path, flags)
    metadata_payload: bytes | None = None
    try:
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_nlink != 1
            or opened_stat.st_dev != package_stat.st_dev
            or opened_stat.st_ino != package_stat.st_ino
            or opened_stat.st_size > limits.max_compressed_bytes
        ):
            raise ValueError("downloaded Helm package identity changed before inspection")
        with (
            os.fdopen(descriptor, "rb", closefd=False) as package,
            open_bounded_tar_gz(
                package,
                max_uncompressed_bytes=limits.max_tar_bytes,
                label="downloaded Helm package",
            ) as bundle,
        ):
            member_count = 0
            expanded_bytes = 0
            for member in bundle:
                member_count += 1
                if member_count > limits.max_members:
                    raise ValueError("downloaded Helm package exceeds the file-count limit")
                if member.isdir():
                    continue
                if not member.isreg() or member.size < 0:
                    raise ValueError("downloaded Helm package contains a non-regular member")
                expanded_bytes += member.size
                if (
                    member.size > limits.max_member_bytes
                    or expanded_bytes > limits.max_expanded_bytes
                ):
                    raise ValueError("downloaded Helm package exceeds the expanded-size limit")
                if not (member.name.count("/") == 1 and member.name.endswith("/Chart.yaml")):
                    continue
                if metadata_payload is not None:
                    raise ValueError("downloaded Helm package has no unique Chart.yaml")
                if member.size > limits.max_metadata_bytes:
                    raise ValueError("downloaded Helm package Chart.yaml exceeds the size limit")
                source = bundle.extractfile(member)
                if source is None:
                    raise ValueError("downloaded Helm package Chart.yaml is unreadable")
                with source:
                    metadata_payload = source.read(limits.max_metadata_bytes + 1)
                if len(metadata_payload) != member.size:
                    raise ValueError("downloaded Helm package Chart.yaml is truncated")
    finally:
        os.close(descriptor)

    if metadata_payload is None:
        raise ValueError("downloaded Helm package has no unique Chart.yaml")
    payload = yaml.safe_load(metadata_payload)
    if not isinstance(payload, Mapping):
        raise ValueError("downloaded Helm package Chart.yaml is invalid")
    return payload


def _pull_chart_once(
    *,
    helm: str,
    chart: str,
    version: str,
    repository: str,
    destination: Path,
) -> tuple[str, str, str | None]:
    repository = _validated_chart_repository(repository)
    if repository.startswith("oci://"):
        command = [
            helm,
            "pull",
            f"{repository.rstrip('/')}/{chart}",
            "--version",
            version,
            "--destination",
            str(destination),
        ]
    else:
        command = [
            helm,
            "pull",
            chart,
            "--repo",
            repository,
            "--version",
            version,
            "--destination",
            str(destination),
        ]
    result = _run(command, label=f"download official chart {chart} {version}")
    packages = list(destination.glob("*.tgz"))
    if len(packages) != 1:
        raise ValueError(f"chart download for {chart} produced an unexpected package set")
    package = packages[0]
    metadata = _chart_metadata_from_package(package)
    resolved_name = str(metadata.get("name") or "")
    resolved_version = str(metadata.get("version") or "")
    if resolved_name != chart or not resolved_version:
        raise ValueError(f"downloaded chart identity differs from requested chart {chart}")
    output = "\n".join((result.stdout or "", result.stderr or ""))
    digest_match = _DIGEST_RE.search(output)
    oci_digest = digest_match.group(1) if digest_match else None
    if repository.startswith("oci://") and oci_digest is None:
        raise ValueError(f"Helm did not report an OCI digest for {chart}")
    return resolved_version, _sha256_file(package), oci_digest


def _transient_chart_pull_reason(exc: BaseException) -> str | None:
    if isinstance(exc, subprocess.TimeoutExpired):
        return "network timeout"
    if not isinstance(exc, RuntimeError):
        return None
    detail = str(exc).lower()
    permanent_markers = (
        "unauthorized",
        "forbidden",
        "authentication",
        "certificate",
        "x509",
        "tls verification",
        "not found",
        "404",
        "digest",
        "identity",
    )
    if any(marker in detail for marker in permanent_markers):
        return None
    reason_markers = (
        ("operation timed out", "network timeout"),
        ("timed out after", "network timeout"),
        ("i/o timeout", "network timeout"),
        ("timeout awaiting response", "network timeout"),
        ("connection reset", "connection reset"),
        ("connection refused", "connection refused"),
        ("unexpected eof", "connection closed unexpectedly"),
        ("network is unreachable", "network unreachable"),
        ("temporary failure", "temporary network failure"),
        ("too many requests", "registry rate limited"),
        (" 429", "registry rate limited"),
        (" 500", "remote server error"),
        (" 502", "remote server error"),
        (" 503", "remote server unavailable"),
        (" 504", "remote server timeout"),
    )
    return next((reason for marker, reason in reason_markers if marker in detail), None)


@contextmanager
def _downloaded_chart(
    *,
    helm: str,
    chart: str,
    version: str,
    repository: str,
    destination: Path,
    emit: Callable[[str], None] | None = None,
) -> Iterator[tuple[Path, tuple[str, str, str | None]]]:
    """Keep one verified package alive through consumption, then remove it."""

    destination.mkdir(parents=True, exist_ok=True)
    last_reason = "transient network failure"
    for attempt in range(1, _CHART_PULL_ATTEMPTS + 1):
        _notify(
            emit,
            f"Downloading and verifying chart {chart} {version} "
            f"(attempt {attempt}/{_CHART_PULL_ATTEMPTS})",
        )
        attempt_dir = Path(
            tempfile.mkdtemp(
                prefix=f".{chart}-pull-{attempt}-",
                dir=destination,
            )
        )
        try:
            try:
                result = _pull_chart_once(
                    helm=helm,
                    chart=chart,
                    version=version,
                    repository=repository,
                    destination=attempt_dir,
                )
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                reason = _transient_chart_pull_reason(exc)
                if reason is None:
                    raise
                last_reason = reason
                if attempt >= _CHART_PULL_ATTEMPTS:
                    raise RuntimeError(
                        f"download official chart {chart} {version} failed after "
                        f"{_CHART_PULL_ATTEMPTS} attempts: {last_reason}"
                    ) from None
                _notify(
                    emit,
                    f"Retrying chart {chart} after {reason} "
                    f"(attempt {attempt + 1}/{_CHART_PULL_ATTEMPTS})",
                )
            else:
                # Caller failures must propagate, never enter acquisition retries.
                yield attempt_dir, result
                return
        finally:
            shutil.rmtree(attempt_dir, ignore_errors=True)
        delay = _CHART_PULL_BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
        delay += random.uniform(0.0, _CHART_PULL_MAX_JITTER_SECONDS)
        time.sleep(delay)
    raise AssertionError("unreachable chart pull retry state")


def _pull_chart(
    *,
    helm: str,
    chart: str,
    version: str,
    repository: str,
    destination: Path,
    emit: Callable[[str], None] | None = None,
) -> tuple[str, str, str | None]:
    """Collect verified package metadata without retaining the downloaded bytes."""
    with _downloaded_chart(
        helm=helm,
        chart=chart,
        version=version,
        repository=repository,
        destination=destination,
        emit=emit,
    ) as (_, metadata):
        return metadata


def _validated_chart_repository(repository: str) -> str:
    parsed = urllib.parse.urlsplit(str(repository or "").strip().rstrip("/"))
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Soperator chart repository URL is invalid") from exc
    if (
        parsed.scheme not in {"https", "oci"}
        or not parsed.hostname
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Soperator chart repository must be a credential-free HTTPS or OCI URL")
    return urllib.parse.urlunsplit(parsed)


def _render_upstream_umbrella(source_root: Path, *, helm: str) -> list[dict[str, Any]]:
    chart = source_root / "helm" / "soperator-fluxcd"
    # A known optional can hide another default child. Collect both inventories;
    # runtime rendering still selects the actual graph from effective values.
    supported_optional_releases = (
        "nodesets.enabled=true",
        "storageClasses.enabled=true",
        "backup.enabled=true",
        "backup.config.enabled=true",
        "notifier.enabled=true",
        "notifier.values.slack.webhookUrl=https://example.invalid",
        "soperator.monitoringDashboards.enabled=true",
    )
    set_arguments = [
        argument for value in supported_optional_releases for argument in ("--set", value)
    ]
    inventory: dict[tuple[str, str, str], dict[str, Any]] = {}
    authorities: dict[tuple[str, str, str], tuple[str, str, str] | str] = {}
    for arguments in (set_arguments, []):
        result = _run(
            [
                helm,
                "template",
                "soperator-fluxcd",
                str(chart),
                "--namespace",
                "flux-system",
                *arguments,
            ],
            label="render verified upstream Soperator umbrella",
        )
        documents = [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]
        repositories = _rendered_repositories(documents)
        seen: set[tuple[str, str, str]] = set()
        for document in documents:
            kind = str(document.get("kind") or "")
            if kind not in {"HelmRelease", "HelmRepository", "OCIRepository"}:
                continue
            metadata = document.get("metadata")
            if not isinstance(metadata, Mapping) or not metadata.get("name"):
                raise ValueError("upstream chart inventory has invalid metadata")
            identity = (
                kind,
                str(metadata.get("namespace") or "flux-system"),
                str(metadata["name"]),
            )
            if identity in seen:
                raise ValueError("upstream chart inventory has duplicate identities")
            seen.add(identity)
            authority = (
                _release_chart_identity(document, repositories)
                if kind == "HelmRelease"
                else repositories[identity]
            )
            if kind == "HelmRelease":
                _release_chart_dependencies(document)
            previous = inventory.get(identity)
            if previous is not None:
                if authorities[identity] != authority:
                    raise ValueError("upstream chart authority differs between supported renders")
            else:
                inventory[identity] = document
                authorities[identity] = authority
        # Keep the first render's edges for shared inventory nodes. Defaults may
        # reference disabled optionals; runtime validates the effective graph.
    return list(inventory.values())


def _source_chart_dependencies(source_chart: Path) -> tuple[tuple[str, str, str], ...]:
    metadata = yaml.safe_load((source_chart / "Chart.yaml").read_text(encoding="utf-8"))
    dependencies = metadata.get("dependencies", [])
    if not isinstance(dependencies, list):
        raise ValueError(f"source chart {source_chart.name} has invalid dependencies")
    identities = []
    for dependency in dependencies:
        if not isinstance(dependency, Mapping):
            raise ValueError(f"source chart {source_chart.name} has invalid dependency metadata")
        name = str(dependency.get("name") or "")
        version = str(dependency.get("version") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name) or not version:
            raise ValueError(f"source chart {source_chart.name} has an invalid dependency identity")
        repository = _validated_chart_repository(str(dependency.get("repository") or ""))
        identities.append((name, version, repository))
    return tuple(identities)


def _rendered_repositories(
    documents: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str], str]:
    repositories: dict[tuple[str, str, str], str] = {}
    for document in documents:
        if document.get("kind") not in {"HelmRepository", "OCIRepository"}:
            continue
        metadata, spec = document.get("metadata"), document.get("spec")
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            raise ValueError("upstream chart repository has invalid metadata")
        name = str(metadata.get("name") or "")
        namespace = str(metadata.get("namespace") or "flux-system")
        identity = (str(document["kind"]), namespace, name)
        if not name or identity in repositories:
            raise ValueError("upstream chart repository identities are ambiguous")
        repositories[identity] = _validated_chart_repository(str(spec.get("url") or ""))
    return repositories


def _release_chart_identity(
    document: Mapping[str, Any],
    repositories: Mapping[tuple[str, str, str], str],
) -> tuple[str, str, str]:
    if document.get("apiVersion") != "helm.toolkit.fluxcd.io/v2":
        raise ValueError("upstream HelmRelease uses an unsupported API contract")
    metadata = document.get("metadata")
    namespace = (
        str(metadata.get("namespace") or "flux-system")
        if isinstance(metadata, Mapping)
        else "flux-system"
    )
    spec = document.get("spec")
    chart_wrapper = spec.get("chart") if isinstance(spec, Mapping) else None
    chart_spec = chart_wrapper.get("spec") if isinstance(chart_wrapper, Mapping) else None
    if not isinstance(chart_spec, Mapping):
        raise ValueError("upstream HelmRelease has no supported chart specification")
    source_ref = chart_spec.get("sourceRef")
    if not isinstance(source_ref, Mapping) or source_ref.get("kind") != "HelmRepository":
        raise ValueError("upstream HelmRelease uses an unsupported chart source kind")
    identity = (
        "HelmRepository",
        str(source_ref.get("namespace") or namespace),
        str(source_ref.get("name") or ""),
    )
    repository = repositories.get(identity, "")
    chart = str(chart_spec.get("chart") or "")
    version = str(chart_spec.get("version") or "")
    if not repository or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", chart) or not version:
        raise ValueError("upstream HelmRelease has an unresolved chart authority")
    return chart, version, repository


def _release_chart_dependencies(document: Mapping[str, Any]) -> tuple[str, ...]:
    metadata, spec = document["metadata"], document["spec"]
    namespace = str(metadata.get("namespace") or "flux-system")
    required = spec.get("dependsOn", [])
    if not isinstance(required, list) or any(
        not isinstance(item, Mapping)
        or set(item) - {"name", "namespace"}
        or not item.get("name")
        or item.get("namespace", namespace) != namespace
        for item in required
    ):
        raise ValueError(
            f"upstream HelmRelease {metadata.get('name')} has unsupported dependencies"
        )
    return tuple(str(item["name"]) for item in required)


def _topological_stages(
    dependencies: Mapping[str, tuple[str, ...]],
) -> dict[str, int]:
    stages: dict[str, int] = {}
    pending = dict(dependencies)
    while pending:
        progressed = False
        for name, required in tuple(pending.items()):
            unknown = set(required) - dependencies.keys()
            if unknown:
                raise ValueError(f"upstream HelmRelease {name} has unknown dependencies")
            if all(dependency in stages for dependency in required):
                stages[name] = 0 if not required else max(stages[item] for item in required) + 1
                pending.pop(name)
                progressed = True
        if not progressed:
            raise ValueError("upstream Soperator HelmRelease graph contains a cycle")
    return stages


def _source_image_references(source_root: Path) -> tuple[str, ...]:
    references: set[str] = set()
    for path in source_root.glob("helm/**/*.yaml"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        references.update(_IMAGE_RE.findall(text))
    references.add(SOPERATOR_ADAPTER_MOUNT_IMAGE)
    return tuple(sorted(references))


def _populate_jail_identity(source_root: Path) -> tuple[str, str]:
    values_path = source_root / "helm" / "slurm-cluster" / "values.yaml"
    payload = yaml.safe_load(values_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("upstream slurm-cluster values are invalid")
    images = payload.get("images")
    if not isinstance(images, Mapping):
        raise ValueError("upstream slurm-cluster values have no image contract")
    explicit = str(images.get("populateJail") or "").strip()
    cuda = str(payload.get("cudaVersion") or "").strip()
    if explicit:
        image = explicit
    else:
        repository = str(images.get("populateJailRepository") or "").strip()
        tag = str(images.get("populateJailTag") or "").strip()
        if not repository or not tag or not cuda:
            raise ValueError("upstream slurm-cluster populate-jail image contract is incomplete")
        image = f"{repository}:{tag}-cuda{cuda}"
    if not is_immutable_oci_image_reference(image):
        image = resolve_oci_image(image).immutable_reference
    return image, cuda


def build_soperator_release_snapshot(
    source: VerifiedSoperatorSource,
    request: SoperatorArtifactRequest,
    *,
    cache_root: Path | None = None,
    emit: Callable[[str], None] | None = None,
) -> SoperatorReleaseSnapshot:
    """Admit the complete required target inventory before sealing evidence."""
    from .soperator_artifact_selection import build_required_snapshot

    return build_required_snapshot(source, request, cache_root=cache_root, emit=emit)


def _snapshot_cache_root(cache_root: Path | None) -> Path:
    return prepare_private_cache_root(
        (cache_root or default_soperator_source_cache_root()).expanduser() / "snapshots-v3"
    )


def _recent_release_snapshot_path(
    request_sha256: str,
    *,
    cache_root: Path | None,
) -> Path:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", request_sha256):
        raise ValueError("Invalid Soperator admission request digest")
    return _snapshot_cache_root(cache_root) / f"{request_sha256.removeprefix('sha256:')}.json"


def _load_recent_release_snapshot(
    request_sha256: str,
    *,
    cache_root: Path | None,
    now: float | None = None,
) -> SoperatorReleaseSnapshot | None:
    path = _recent_release_snapshot_path(request_sha256, cache_root=cache_root)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("cached Soperator release snapshot is unsafe")
    age = (time.time() if now is None else now) - info.st_mtime
    if age < 0:
        raise ValueError("cached Soperator release snapshot has a future timestamp")
    if age > _RECENT_RELEASE_SNAPSHOT_MAX_AGE_SECONDS:
        return None
    snapshot = load_soperator_release_snapshot(path)
    if snapshot.request_sha256 != request_sha256:
        raise ValueError("cached Soperator admission request differs")
    if snapshot.mount_image != SOPERATOR_ADAPTER_MOUNT_IMAGE:
        return None
    return snapshot


def _retain_release_snapshot(
    snapshot: SoperatorReleaseSnapshot, *, cache_root: Path | None
) -> None:
    snapshot = seal_soperator_release_snapshot(snapshot)
    root = prepare_private_cache_root(_snapshot_cache_root(cache_root) / "by-digest")
    path = root / f"{snapshot.snapshot_sha256.removeprefix('sha256:')}.json"
    if path.exists() or path.is_symlink():
        if load_soperator_release_snapshot(path) != snapshot:
            raise ValueError("retained Soperator snapshot differs from its identity")
    else:
        write_soperator_release_snapshot(path, snapshot)


def _write_recent_release_snapshot(
    snapshot: SoperatorReleaseSnapshot,
    *,
    cache_root: Path | None,
) -> Path:
    snapshot = seal_soperator_release_snapshot(snapshot)
    _retain_release_snapshot(snapshot, cache_root=cache_root)
    path = _recent_release_snapshot_path(snapshot.request_sha256, cache_root=cache_root)
    write_soperator_release_snapshot(path, snapshot)
    return path


def _load_frozen_release_snapshot(
    selector: str,
    digest: str,
    *,
    cache_root: Path | None,
    target_ref: str,
) -> SoperatorReleaseSnapshot:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("invalid frozen Soperator release snapshot digest")
    bound = (_FROZEN_RELEASES.get() or {}).get(target_ref)
    if bound is not None:
        if bound.snapshot.snapshot_sha256 != digest:
            raise ValueError("Bound Soperator snapshot digest differs from requested authority")
        sealed = seal_soperator_release_snapshot(bound.snapshot)
        if selector not in {"latest", sealed.release}:
            raise ValueError("Bound Soperator snapshot release differs")
        return sealed
    path = _snapshot_cache_root(cache_root) / "by-digest" / f"{digest.removeprefix('sha256:')}.json"
    if not path.exists():
        raise RuntimeError("the approved Soperator release snapshot content is unavailable")
    snapshot = load_soperator_release_snapshot(path)
    if snapshot.snapshot_sha256 != digest or selector not in {"latest", snapshot.release}:
        raise ValueError("retained Soperator release snapshot identity differs")
    if snapshot.target_ref != target_ref:
        raise ValueError("retained Soperator release snapshot target differs")
    return snapshot


def freeze_soperator_release(
    selector: str | None = "latest",
    *,
    request: SoperatorArtifactRequest | None = None,
    source: VerifiedSoperatorSource | None = None,
    target_ref: str | None = None,
    current_release: str | None = None,
    cache_root: Path | None = None,
    identity_root: Path | None = None,
    snapshot_sha256: str | None = None,
    opener: Any = None,
    emit: Callable[[str], None] | None = None,
) -> FrozenSoperatorRelease:
    """Admit requested artifacts, or reverify one exact captured operation."""
    from .soperator_artifact_selection import compile_required_stages
    from .soperator_release_artifacts import verify_soperator_release_artifacts

    normalized = normalize_soperator_release_selector(selector)
    if snapshot_sha256 is not None:
        if not target_ref:
            raise ValueError("Frozen Soperator snapshot replay requires its target identity")
        snapshot = _load_frozen_release_snapshot(
            normalized,
            snapshot_sha256,
            cache_root=cache_root,
            target_ref=target_ref,
        )
        if current_release and SoperatorVersion.parse(snapshot.release) < SoperatorVersion.parse(
            current_release
        ):
            raise ValueError(
                f"Soperator downgrade {current_release} -> {snapshot.release} is not supported"
            )
        frozen = frozen_soperator_release_from_snapshot(snapshot, cache_root=cache_root)
        with SoperatorReleaseIdentityLedger(identity_root).locked(frozen.metadata):
            pass
        verify_soperator_release_artifacts(snapshot, frozen.source, cache_root=cache_root)
        _retain_release_snapshot(snapshot, cache_root=cache_root)
        return frozen
    if request is None:
        raise ValueError("Soperator package admission requires a target configuration request")
    source = source or resolve_soperator_source(
        normalized,
        cache_root=cache_root,
        identity_root=identity_root,
        opener=opener,
        emit=emit,
    )
    if normalized not in {"latest", source.release}:
        raise ValueError("Soperator source differs from the requested release")
    if current_release and SoperatorVersion.parse(source.release) < SoperatorVersion.parse(
        current_release
    ):
        raise ValueError(
            f"Soperator downgrade {current_release} -> {source.release} is not supported"
        )
    digest = request.fingerprint(source.identity_sha256)
    cached = (
        _load_recent_release_snapshot(digest, cache_root=cache_root) if opener is None else None
    )
    if cached is not None:
        if cached.target_ref != request.target_ref or cached.commit != source.metadata.commit:
            raise ValueError("Cached Soperator source or target differs from admission")
        for stage, (values, _) in compile_required_stages(source, request, cached).items():
            graph = cached.release_graph if stage == "desired" else cached.stage_graphs[stage]
            from .soperator_adapter import render_soperator_adapter_documents
            from .soperator_values import with_source_observability

            adapter_docs, _ = render_soperator_adapter_documents(
                with_source_observability(request.stages[stage], source),
                release=cached,
            )
            verify_soperator_release_artifacts(
                replace(cached, release_graph=graph),
                source.source,
                cache_root=cache_root,
                values=values,
                post_render_patches=request.post_render_patches,
                adapter_documents=adapter_docs,
            )
        snapshot = cached
    else:
        snapshot = build_soperator_release_snapshot(
            source, request, cache_root=cache_root, emit=emit
        )
    snapshot = seal_soperator_release_snapshot(snapshot)
    ledger = SoperatorReleaseIdentityLedger(identity_root)
    with ledger.locked(source.metadata) as identity:
        fresh = resolve_soperator_release(source.release, opener=opener)
        if (fresh.commit, fresh.tree) != (source.metadata.commit, source.metadata.tree):
            raise ValueError("official Soperator release tag moved during artifact admission")
        ledger.record(identity)
        _write_recent_release_snapshot(snapshot, cache_root=cache_root)
    return FrozenSoperatorRelease(source.metadata, source.source, snapshot)


def frozen_soperator_release_from_snapshot(
    snapshot: SoperatorReleaseSnapshot,
    *,
    cache_root: Path | None = None,
) -> FrozenSoperatorRelease:
    """Rehydrate an already-frozen target without resolving its selector again."""

    sealed = seal_soperator_release_snapshot(snapshot)
    source = ensure_soperator_release_source(sealed, cache_root=cache_root)
    metadata = SoperatorReleaseMetadata(
        selector=sealed.selector,
        release=sealed.release,
        repository=sealed.repository,
        tag=sealed.tag,
        commit=sealed.commit,
        tree=sealed.tree,
        archive_url=sealed.archive_url,
        archive_root=sealed.archive_root,
        published_at="",
        tree_entries=(),
    )
    return FrozenSoperatorRelease(metadata=metadata, source=source, snapshot=sealed)


def inspect_soperator_release_contract(
    release: str,
    *,
    cache_root: Path | None = None,
    opener: Any = None,
) -> tuple[SoperatorReleaseMetadata, SoperatorSourceReceipt, str, str]:
    """Resolve source-derived facts for an installed stable release."""

    metadata = resolve_soperator_release(release, opener=opener)
    source = acquire_soperator_release_source(metadata, cache_root=cache_root, opener=opener)
    contract, capability_sha256 = classify_soperator_release_capabilities(Path(source.source_dir))
    return metadata, source, contract, capability_sha256


__all__ = [
    "FrozenSoperatorRelease",
    "build_soperator_release_snapshot",
    "current_frozen_soperator_release",
    "freeze_soperator_release",
    "frozen_soperator_release_from_snapshot",
    "inspect_soperator_release_contract",
    "use_frozen_soperator_release",
]
