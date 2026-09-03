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
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from .archive_safety import open_bounded_tar_gz
from .oci_image import is_immutable_oci_image_reference, resolve_oci_image
from .soperator_cache import prepare_private_cache_root
from .soperator_release import (
    SOPERATOR_ADAPTER_MOUNT_IMAGE,
    SOPERATOR_MAIN_RELEASE_NAME,
    SOPERATOR_RELEASE_SNAPSHOT_SCHEMA,
    SOPERATOR_UPSTREAM_CHART_ROLES,
    SOPERATOR_UPSTREAM_REGISTRY,
    SoperatorChartSnapshot,
    SoperatorReleaseGraphNode,
    SoperatorReleaseMetadata,
    SoperatorReleaseSnapshot,
    SoperatorThirdPartyChartSnapshot,
    SoperatorVersion,
    classify_soperator_release_capabilities,
    load_soperator_release_snapshot,
    normalize_soperator_release_selector,
    resolve_soperator_release,
    seal_soperator_release_snapshot,
    write_soperator_release_snapshot,
)
from .soperator_release_identity import SoperatorReleaseIdentityLedger
from .soperator_release_source import (
    SoperatorSourceReceipt,
    acquire_soperator_release_source,
    default_soperator_source_cache_root,
    ensure_soperator_release_source,
    normalized_script_manifest,
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


_FROZEN_RELEASE: ContextVar[FrozenSoperatorRelease | None] = ContextVar(
    "nebius_cxcli_frozen_soperator_release",
    default=None,
)


def current_frozen_soperator_release(release: str) -> FrozenSoperatorRelease | None:
    frozen = _FROZEN_RELEASE.get()
    if frozen is not None and frozen.snapshot.release == release:
        return frozen
    return None


@contextmanager
def use_frozen_soperator_release(frozen: FrozenSoperatorRelease):
    token = _FROZEN_RELEASE.set(frozen)
    try:
        yield frozen
    finally:
        _FROZEN_RELEASE.reset(token)


def _run(command: Sequence[str], *, label: str) -> subprocess.CompletedProcess[str]:
    timeout = 300
    try:
        result = subprocess.run(
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


def _pull_chart(
    *,
    helm: str,
    chart: str,
    version: str,
    repository: str,
    destination: Path,
    emit: Callable[[str], None] | None = None,
) -> tuple[str, str, str | None]:
    """Pull and validate one chart with bounded transient-only retries."""

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
            return _pull_chart_once(
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
        finally:
            shutil.rmtree(attempt_dir, ignore_errors=True)
        delay = _CHART_PULL_BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
        delay += random.uniform(0.0, _CHART_PULL_MAX_JITTER_SECONDS)
        time.sleep(delay)
    raise AssertionError("unreachable chart pull retry state")


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
    # Freeze the complete adapter-supported graph, not only upstream defaults.
    # Runtime rendering selects a subset from this immutable superset.
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
    result = _run(
        [
            helm,
            "template",
            "soperator-fluxcd",
            str(chart),
            "--namespace",
            "flux-system",
            *set_arguments,
        ],
        label="render verified upstream Soperator umbrella",
    )
    return [
        document for document in yaml.safe_load_all(result.stdout) if isinstance(document, dict)
    ]


def _rendered_repositories(documents: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    repositories: dict[str, str] = {}
    for document in documents:
        if document.get("kind") not in {"HelmRepository", "OCIRepository"}:
            continue
        metadata = document.get("metadata")
        spec = document.get("spec")
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            continue
        name = str(metadata.get("name") or "")
        url = str(spec.get("url") or "").rstrip("/")
        if name and url:
            repositories[name] = _validated_chart_repository(url)
    return repositories


def _release_chart_identity(
    document: Mapping[str, Any],
    repositories: Mapping[str, str],
) -> tuple[str, str, str]:
    spec = document.get("spec")
    chart_wrapper = spec.get("chart") if isinstance(spec, Mapping) else None
    chart_spec = chart_wrapper.get("spec") if isinstance(chart_wrapper, Mapping) else None
    if not isinstance(chart_spec, Mapping):
        raise ValueError("upstream HelmRelease has no chart specification")
    source_ref = chart_spec.get("sourceRef")
    source_name = str(source_ref.get("name") or "") if isinstance(source_ref, Mapping) else ""
    repository = repositories.get(source_name, "")
    chart = str(chart_spec.get("chart") or "")
    version = str(chart_spec.get("version") or "")
    if not repository or not chart or not version:
        raise ValueError("upstream HelmRelease has an unresolved chart authority")
    return chart, version, repository


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
    metadata: SoperatorReleaseMetadata,
    source: SoperatorSourceReceipt,
    *,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
    emit: Callable[[str], None] | None = None,
) -> SoperatorReleaseSnapshot:
    """Resolve all chart/package identities and seal one operation snapshot."""

    del runner  # Reserved injection boundary; subprocess execution remains centralized in _run.
    if (
        source.release != metadata.release
        or source.commit != metadata.commit
        or source.tree != metadata.tree
    ):
        raise ValueError("Soperator source receipt does not match resolved release metadata")
    helm = shutil.which("helm")
    if not helm:
        raise RuntimeError("helm is required to freeze a Soperator release snapshot")
    source_root = Path(source.source_dir).resolve(strict=True)
    capability_contract, capability_sha256 = classify_soperator_release_capabilities(source_root)
    if capability_contract != "upstream-flux-v1":
        raise ValueError(
            "the requested target release does not implement the supported upstream Flux contract"
        )
    _notify(emit, "Rendering the verified upstream Soperator chart graph")
    documents = _render_upstream_umbrella(source_root, helm=helm)
    repositories = _rendered_repositories(documents)
    helm_releases = [item for item in documents if item.get("kind") == "HelmRelease"]
    if not helm_releases:
        raise ValueError("verified upstream umbrella rendered no HelmRelease graph")

    chart_identities: dict[str, tuple[str, str, str]] = {}
    release_dependencies: dict[str, tuple[str, ...]] = {}
    release_namespaces: dict[str, str] = {}
    for document in helm_releases:
        metadata_row = document.get("metadata")
        if not isinstance(metadata_row, Mapping):
            raise ValueError("upstream HelmRelease has no metadata")
        name = str(metadata_row.get("name") or "")
        namespace = str(metadata_row.get("namespace") or "flux-system")
        if not name or name in chart_identities:
            raise ValueError("upstream HelmRelease identities are ambiguous")
        chart_identities[name] = _release_chart_identity(document, repositories)
        spec = document.get("spec")
        depends_on = spec.get("dependsOn", []) if isinstance(spec, Mapping) else []
        if not isinstance(depends_on, list):
            raise ValueError(f"upstream HelmRelease {name} has invalid dependencies")
        release_dependencies[name] = tuple(
            str(item.get("name") or "")
            for item in depends_on
            if isinstance(item, Mapping) and str(item.get("name") or "")
        )
        release_namespaces[name] = namespace
    stages = _topological_stages(release_dependencies)

    charts: dict[str, SoperatorChartSnapshot] = {}
    third_party: dict[str, SoperatorThirdPartyChartSnapshot] = {}
    graph: list[SoperatorReleaseGraphNode] = []
    with tempfile.TemporaryDirectory(prefix="nebius-cxcli-soperator-freeze-") as value:
        temp = Path(value)
        for chart_name, key in _CHART_KEY_BY_NAME.items():
            source_path = source_root / "helm" / chart_name.removeprefix("helm-")
            if not (source_path / "Chart.yaml").is_file():
                # Chart directory names are not always the chart name without its prefix.
                matches = [
                    path.parent
                    for path in source_root.glob("helm/*/Chart.yaml")
                    if isinstance(
                        (chart_payload := yaml.safe_load(path.read_text(encoding="utf-8"))),
                        Mapping,
                    )
                    and str(chart_payload.get("name") or "") == chart_name
                ]
                if len(matches) != 1:
                    raise ValueError(
                        f"unknown target capability: required chart {chart_name} is absent"
                    )
                source_path = matches[0]
            source_chart = yaml.safe_load((source_path / "Chart.yaml").read_text(encoding="utf-8"))
            if not isinstance(source_chart, Mapping):
                raise ValueError(f"upstream source chart {chart_name} has invalid metadata")
            source_chart_name = str(source_chart.get("name") or "")
            source_chart_version = str(source_chart.get("version") or "")
            if source_chart_name != chart_name or not source_chart_version:
                raise ValueError(f"upstream source chart {chart_name} has an invalid identity")
            source_tree_sha256, _ = normalized_tree_manifest(source_path)
            destination = temp / f"upstream-{key}"
            destination.mkdir()
            version, package_sha256, oci_digest = _pull_chart(
                helm=helm,
                chart=chart_name,
                version=source_chart_version,
                repository=SOPERATOR_UPSTREAM_REGISTRY,
                destination=destination,
                emit=emit,
            )
            if version != source_chart_version or oci_digest is None:
                raise ValueError(f"official upstream chart {chart_name} differs from release")
            charts[key] = SoperatorChartSnapshot(
                name=chart_name,
                version=version,
                digest=oci_digest,
                package_sha256=package_sha256,
                source_path=source_path.relative_to(source_root).as_posix(),
                source_tree_sha256=source_tree_sha256,
            )

        resolved_third_party: dict[tuple[str, str, str], str] = {}
        for release_name, (chart_name, constraint, repository) in chart_identities.items():
            if repository == SOPERATOR_UPSTREAM_REGISTRY:
                chart_key = _CHART_KEY_BY_NAME.get(chart_name)
                owner = "upstream"
                if chart_key is None:
                    raise ValueError(f"unknown upstream chart in release graph: {chart_name}")
            else:
                owner = "third-party"
                chart_key = _THIRD_PARTY_KEY_BY_CHART.get(chart_name)
                if chart_key is None:
                    raise ValueError(
                        f"unknown third-party chart in release capability contract: {chart_name}"
                    )
                identity = (chart_name, constraint, repository)
                prior_key = resolved_third_party.get(identity)
                if prior_key is None:
                    destination = temp / f"third-party-{chart_key}"
                    destination.mkdir()
                    version, package_sha256, oci_digest = _pull_chart(
                        helm=helm,
                        chart=chart_name,
                        version=constraint,
                        repository=repository,
                        destination=destination,
                        emit=emit,
                    )
                    third_party[chart_key] = SoperatorThirdPartyChartSnapshot(
                        chart=chart_name,
                        version=version,
                        repository=repository,
                        package_sha256=package_sha256,
                        oci_digest=oci_digest,
                    )
                    resolved_third_party[identity] = chart_key
                else:
                    chart_key = prior_key
            graph.append(
                SoperatorReleaseGraphNode(
                    release_name=release_name,
                    namespace=release_namespaces[release_name],
                    owner=owner,
                    stage=stages[release_name],
                    chart_key=chart_key,
                    dependencies=release_dependencies[release_name],
                    is_main=release_name == SOPERATOR_MAIN_RELEASE_NAME,
                )
            )

    _notify(emit, "Resolving the immutable PopulateJail image and Jail CUDA target")
    scripts_manifest_sha256, _ = normalized_script_manifest(source_root)
    populate_jail_image, jail_cuda_version = _populate_jail_identity(source_root)
    snapshot = SoperatorReleaseSnapshot(
        schema=SOPERATOR_RELEASE_SNAPSHOT_SCHEMA,
        selector=metadata.selector,
        release=metadata.release,
        repository=metadata.repository,
        tag=metadata.tag,
        commit=metadata.commit,
        tree=metadata.tree,
        archive_url=metadata.archive_url,
        archive_sha256=source.archive_sha256,
        archive_root=metadata.archive_root,
        source_manifest_sha256=source.manifest_sha256,
        registry=SOPERATOR_UPSTREAM_REGISTRY,
        capability_contract=capability_contract,
        capability_sha256=capability_sha256,
        charts=charts,
        third_party_charts=third_party,
        release_graph=tuple(sorted(graph, key=lambda item: (item.stage, item.release_name))),
        scripts_manifest_sha256=scripts_manifest_sha256,
        image_references=_source_image_references(source_root),
        mount_image=SOPERATOR_ADAPTER_MOUNT_IMAGE,
        adapter_state_schema="nebius-cxcli.soperator-adapter-state.v2",
        populate_jail_image=populate_jail_image,
        jail_cuda_version=jail_cuda_version,
        snapshot_sha256="",
    )
    return seal_soperator_release_snapshot(snapshot)


def _recent_release_snapshot_path(
    selector: str,
    *,
    cache_root: Path | None,
) -> Path:
    normalized = normalize_soperator_release_selector(selector)
    root = prepare_private_cache_root(
        (cache_root or default_soperator_source_cache_root()).expanduser() / "snapshots"
    )
    return root / f"{normalized}.json"


def _load_recent_release_snapshot(
    selector: str,
    *,
    cache_root: Path | None,
    now: float | None = None,
) -> SoperatorReleaseSnapshot | None:
    normalized = normalize_soperator_release_selector(selector)
    path = _recent_release_snapshot_path(normalized, cache_root=cache_root)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("cached Soperator release snapshot is unsafe")
    observed_at = time.time() if now is None else now
    age = observed_at - info.st_mtime
    if age < 0:
        raise ValueError("cached Soperator release snapshot has a future timestamp")
    if age > _RECENT_RELEASE_SNAPSHOT_MAX_AGE_SECONDS:
        return None
    snapshot = load_soperator_release_snapshot(path)
    if snapshot.selector != normalized or (
        normalized != "latest" and snapshot.release != normalized
    ):
        raise ValueError("cached Soperator release snapshot selector differs")
    if snapshot.mount_image != SOPERATOR_ADAPTER_MOUNT_IMAGE:
        return None
    return snapshot


def _write_recent_release_snapshot(
    snapshot: SoperatorReleaseSnapshot,
    *,
    selector: str,
    cache_root: Path | None,
) -> Path:
    normalized = normalize_soperator_release_selector(selector)
    if normalized != "latest" and snapshot.release != normalized:
        raise ValueError("Soperator release snapshot cannot be cached under another release")
    cached = seal_soperator_release_snapshot(
        replace(snapshot, selector=normalized, snapshot_sha256="")
    )
    path = _recent_release_snapshot_path(normalized, cache_root=cache_root)
    write_soperator_release_snapshot(path, cached)
    return path


def freeze_soperator_release(
    selector: str | None = "latest",
    *,
    current_release: str | None = None,
    cache_root: Path | None = None,
    identity_root: Path | None = None,
    opener: Any = None,
    emit: Callable[[str], None] | None = None,
) -> FrozenSoperatorRelease:
    """Resolve, verify, and freeze a stable official release before mutation."""

    normalized_selector = normalize_soperator_release_selector(selector)
    if opener is None:
        cached_snapshot = _load_recent_release_snapshot(
            normalized_selector,
            cache_root=cache_root,
        )
        if cached_snapshot is not None:
            _notify(
                emit,
                "Cached release snapshot found; re-verifying source and identity",
            )
            if current_release and SoperatorVersion.parse(
                cached_snapshot.release
            ) < SoperatorVersion.parse(current_release):
                raise ValueError(
                    f"Soperator downgrade {current_release} -> "
                    f"{cached_snapshot.release} is not supported"
                )
            frozen = frozen_soperator_release_from_snapshot(
                cached_snapshot,
                cache_root=cache_root,
            )
            ledger = SoperatorReleaseIdentityLedger(identity_root)
            with ledger.locked(frozen.metadata):
                pass
            _notify(emit, "Cached release source and identity re-verified")
            return frozen

    _notify(emit, "Resolving the exact official Soperator release identity")
    metadata = resolve_soperator_release(
        normalized_selector,
        current_release=current_release,
        opener=opener,
    )
    ledger = SoperatorReleaseIdentityLedger(identity_root)
    with ledger.locked(metadata) as release_identity:
        _notify(emit, "Acquiring and verifying the official release source archive")
        source = acquire_soperator_release_source(
            metadata,
            cache_root=cache_root,
            opener=opener,
        )
        snapshot = build_soperator_release_snapshot(metadata, source, emit=emit)
        _notify(emit, "Re-verifying the official release tag and source identity")
        fresh = resolve_soperator_release(metadata.release, opener=opener)
        if (
            fresh.repository,
            fresh.tag,
            fresh.commit,
            fresh.tree,
        ) != (
            metadata.repository,
            metadata.tag,
            metadata.commit,
            metadata.tree,
        ):
            raise RuntimeError(
                "official Soperator release tag moved while its artifacts were being verified"
            )
        ledger.record(release_identity)
    _notify(emit, "Sealing the verified Soperator release snapshot")
    for cache_selector in sorted({normalized_selector, metadata.release}):
        _write_recent_release_snapshot(
            snapshot,
            selector=cache_selector,
            cache_root=cache_root,
        )
    return FrozenSoperatorRelease(metadata=metadata, source=source, snapshot=snapshot)


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
