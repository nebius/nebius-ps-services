"""Dynamic official-upstream release authority for Soperator.

Release selectors are resolved only against the public ``nebius/soperator``
repository.  Discovery is mutable evidence; a :class:`SoperatorReleaseSnapshot`
is the immutable authority persisted by an executing operation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol

import yaml

from .soperator_receipt_io import read_owner_only_json, write_owner_only_json

SOPERATOR_RELEASE_SNAPSHOT_SCHEMA = "nebius-cxcli.soperator-release-snapshot.v2"
SOPERATOR_MAIN_RELEASE_NAME = "soperator-fluxcd-slurm-cluster"
SOPERATOR_UPSTREAM_REPOSITORY = "https://github.com/nebius/soperator"
SOPERATOR_UPSTREAM_API = "https://api.github.com/repos/nebius/soperator"
SOPERATOR_UPSTREAM_REGISTRY = "oci://cr.eu-north1.nebius.cloud/soperator"
SOPERATOR_UPSTREAM_UMBRELLA_CHART = "helm-soperator-fluxcd"
SOPERATOR_UPSTREAM_CHART_ROLES = (
    ("helm-soperator-fluxcd-bootstrap", "bootstrap"),
    (SOPERATOR_UPSTREAM_UMBRELLA_CHART, "umbrella"),
    ("helm-soperator", "operator"),
    ("helm-slurm-cluster", "slurmCluster"),
    ("helm-slurm-cluster-storage", "slurmClusterStorage"),
    ("helm-nodesets", "nodesets"),
    ("helm-soperator-activechecks", "activeChecks"),
    ("helm-soperatorchecks", "checks"),
    ("helm-soperator-backup-config", "backupConfig"),
    ("helm-soperator-notifier", "notifier"),
    ("helm-soperator-dcgm-exporter", "dcgmExporter"),
    ("helm-nodeconfigurator", "nodeConfigurator"),
    ("helm-soperator-monitoring-dashboards", "monitoringDashboards"),
    ("helm-soperator-custom-configmaps", "customConfigMaps"),
    ("helm-storageclasses", "storageClasses"),
    ("helm-nfs-server", "nfsServer"),
)

# This is an adapter-owned utility image, not a product-release selector.  Its
# immutable digest is recorded in every operation snapshot.
SOPERATOR_ADAPTER_MOUNT_IMAGE = (
    "cr.eu-north1.nebius.cloud/soperator/busybox@sha256:"
    "e22b7c1e77bda9f6028d20860ab53257697a9153115f30fd6997df7422e4e845"
)

_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RELATIVE_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_TRANSIENT_GITHUB_STATUS_CODES = frozenset({502, 503, 504})
_GITHUB_REQUEST_ATTEMPTS = 3


class JsonOpener(Protocol):
    def open(self, request: urllib.request.Request, timeout: float = ...) -> Any: ...


@dataclass(frozen=True, order=True)
class SoperatorVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> SoperatorVersion:
        token = str(value or "").strip().removeprefix("v")
        if not _VERSION_RE.fullmatch(token):
            raise ValueError("Soperator release must be `latest` or an exact stable X.Y.Z version")
        major, minor, patch = (int(part) for part in token.split("."))
        return cls(major, minor, patch)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class SoperatorGitTreeEntry:
    path: str
    mode: str
    object_type: str
    sha: str
    size: int | None


@dataclass(frozen=True)
class SoperatorReleaseMetadata:
    selector: str
    release: str
    repository: str
    tag: str
    commit: str
    tree: str
    archive_url: str
    archive_root: str
    published_at: str
    tree_entries: tuple[SoperatorGitTreeEntry, ...]


@dataclass(frozen=True)
class SoperatorChartSnapshot:
    name: str
    version: str
    digest: str
    package_sha256: str
    source_path: str
    source_tree_sha256: str

    @property
    def oci_url(self) -> str:
        return f"{SOPERATOR_UPSTREAM_REGISTRY}/{self.name}"


@dataclass(frozen=True)
class SoperatorThirdPartyChartSnapshot:
    chart: str
    version: str
    repository: str
    package_sha256: str
    oci_digest: str | None = None


@dataclass(frozen=True)
class SoperatorReleaseGraphNode:
    release_name: str
    namespace: str
    owner: str
    stage: int
    chart_key: str
    dependencies: tuple[str, ...]
    is_main: bool


@dataclass(frozen=True)
class SoperatorReleaseSnapshot:
    schema: str
    selector: str
    release: str
    repository: str
    tag: str
    commit: str
    tree: str
    archive_url: str
    archive_sha256: str
    archive_root: str
    source_manifest_sha256: str
    registry: str
    capability_contract: str
    capability_sha256: str
    charts: Mapping[str, SoperatorChartSnapshot]
    third_party_charts: Mapping[str, SoperatorThirdPartyChartSnapshot]
    release_graph: tuple[SoperatorReleaseGraphNode, ...]
    scripts_manifest_sha256: str
    image_references: tuple[str, ...]
    mount_image: str
    adapter_state_schema: str
    populate_jail_image: str
    jail_cuda_version: str
    snapshot_sha256: str

    @property
    def bootstrap(self) -> SoperatorChartSnapshot:
        return self.charts["bootstrap"]

    @property
    def umbrella(self) -> SoperatorChartSnapshot:
        return self.charts["umbrella"]

    def canonical_payload(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if not include_digest:
            payload.pop("snapshot_sha256", None)
        return payload


def normalize_soperator_release_selector(value: str | None) -> str:
    selector = "latest" if value is None else str(value).strip().lower()
    if selector == "latest":
        return selector
    return str(SoperatorVersion.parse(selector))


def _validated_github_api_url(url: str, *, label: str) -> str:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} must use the official GitHub API") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.github.com"
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(f"{label} must use the official GitHub API")
    return urllib.parse.urlunsplit(parsed)


class _OfficialGitHubRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        _validated_github_api_url(newurl, label="redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _github_api_authorization_header() -> str:
    for variable in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = str(os.environ.get(variable) or "").strip()
        if not token:
            continue
        if any(
            character.isspace() or ord(character) < 0x21 or ord(character) == 0x7F
            for character in token
        ):
            raise ValueError(f"{variable} is not a valid GitHub API token value")
        return f"Bearer {token}"
    return ""


def _json_payload_request(
    url: str,
    *,
    opener: JsonOpener | None,
    label: str,
) -> Any:
    validated_url = _validated_github_api_url(url, label=label)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "nebius-cxcli",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if opener is None:
        authorization = _github_api_authorization_header()
        if authorization:
            headers["Authorization"] = authorization
    request = urllib.request.Request(
        validated_url,
        headers=headers,
    )
    client = opener or urllib.request.build_opener(_OfficialGitHubRedirectHandler())
    response = None
    for attempt in range(_GITHUB_REQUEST_ATTEMPTS):
        try:
            response = client.open(request, timeout=30)
            break
        except urllib.error.HTTPError as exc:
            retryable = exc.code in _TRANSIENT_GITHUB_STATUS_CODES
            if not retryable or attempt + 1 == _GITHUB_REQUEST_ATTEMPTS:
                raise RuntimeError(
                    f"could not resolve {label} from official GitHub: {exc}"
                ) from exc
            time.sleep(float(2**attempt))
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"could not resolve {label} from official GitHub: {exc}") from exc
    if response is None:  # pragma: no cover - loop exhaustiveness guard
        raise RuntimeError(f"could not resolve {label} from official GitHub")
    with response:
        try:
            _validated_github_api_url(
                str(response.geturl() or ""),
                label=f"{label} response URL",
            )
        except ValueError as exc:
            raise RuntimeError(f"{label} resolution left the official GitHub API") from exc
        try:
            payload = json.loads(response.read())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"official GitHub returned invalid {label} metadata") from exc
    return payload


def _json_request(
    url: str,
    *,
    opener: JsonOpener | None,
    label: str,
) -> Mapping[str, Any]:
    payload = _json_payload_request(url, opener=opener, label=label)
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"official GitHub returned invalid {label} metadata")
    return payload


def _json_sequence_request(
    url: str,
    *,
    opener: JsonOpener | None,
    label: str,
) -> Sequence[Any]:
    payload = _json_payload_request(url, opener=opener, label=label)
    if not isinstance(payload, list):
        raise RuntimeError(f"official GitHub returned invalid {label} metadata")
    return payload


def _exact_release_payload(
    release: str,
    *,
    opener: JsonOpener | None,
) -> Mapping[str, Any]:
    endpoint = f"{SOPERATOR_UPSTREAM_API}/releases/tags/{urllib.parse.quote(release, safe='')}"
    try:
        return _json_request(
            endpoint,
            opener=opener,
            label=f"Soperator release {release}",
        )
    except RuntimeError as exc:
        cause = exc.__cause__
        if not (
            isinstance(cause, urllib.error.HTTPError)
            and cause.code in _TRANSIENT_GITHUB_STATUS_CODES
        ):
            raise
        endpoint_error = exc

    page = 1
    while True:
        releases = _json_sequence_request(
            f"{SOPERATOR_UPSTREAM_API}/releases?per_page=100&page={page}",
            opener=opener,
            label="Soperator releases",
        )
        matches = [
            item
            for item in releases
            if isinstance(item, Mapping)
            and str(item.get("tag_name") or "").strip().removeprefix("v") == release
        ]
        if len(matches) > 1:
            raise RuntimeError(
                f"official GitHub returned ambiguous Soperator release {release} metadata"
            )
        if matches:
            return matches[0]
        if len(releases) < 100:
            break
        page += 1
    raise RuntimeError(
        f"could not resolve Soperator release {release} from official GitHub"
    ) from endpoint_error


def _git_ref_identity(
    tag: str,
    *,
    opener: JsonOpener | None,
) -> tuple[str, str, tuple[SoperatorGitTreeEntry, ...]]:
    encoded_tag = urllib.parse.quote(tag, safe="")
    ref = _json_request(
        f"{SOPERATOR_UPSTREAM_API}/git/ref/tags/{encoded_tag}",
        opener=opener,
        label="Soperator tag",
    )
    obj = ref.get("object")
    if not isinstance(obj, Mapping):
        raise RuntimeError("official Soperator tag has no Git object identity")
    object_type = str(obj.get("type") or "")
    object_sha = str(obj.get("sha") or "")
    if object_type == "tag":
        annotated = _json_request(
            f"{SOPERATOR_UPSTREAM_API}/git/tags/{object_sha}",
            opener=opener,
            label="annotated Soperator tag",
        )
        obj = annotated.get("object")
        if not isinstance(obj, Mapping):
            raise RuntimeError("annotated Soperator tag has no target identity")
        object_type = str(obj.get("type") or "")
        object_sha = str(obj.get("sha") or "")
    if object_type != "commit" or not _GIT_OBJECT_RE.fullmatch(object_sha):
        raise RuntimeError("official Soperator release tag does not resolve to one commit")
    commit_payload = _json_request(
        f"{SOPERATOR_UPSTREAM_API}/git/commits/{object_sha}",
        opener=opener,
        label="Soperator release commit",
    )
    tree = commit_payload.get("tree")
    tree_sha = str(tree.get("sha") or "") if isinstance(tree, Mapping) else ""
    if not _GIT_OBJECT_RE.fullmatch(tree_sha):
        raise RuntimeError("official Soperator release commit has no exact tree identity")
    tree_payload = _json_request(
        f"{SOPERATOR_UPSTREAM_API}/git/trees/{tree_sha}?recursive=1",
        opener=opener,
        label="Soperator release tree",
    )
    if tree_payload.get("truncated") is True:
        raise RuntimeError("official Soperator release tree is truncated")
    raw_entries = tree_payload.get("tree")
    if not isinstance(raw_entries, list):
        raise RuntimeError("official Soperator release tree has no entries")
    entries: list[SoperatorGitTreeEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise RuntimeError("official Soperator release tree contains an invalid entry")
        path = str(raw.get("path") or "")
        mode = str(raw.get("mode") or "")
        entry_type = str(raw.get("type") or "")
        sha = str(raw.get("sha") or "")
        size_value = raw.get("size")
        size = int(size_value) if isinstance(size_value, int) else None
        if (
            not path
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or not _GIT_OBJECT_RE.fullmatch(sha)
        ):
            raise RuntimeError("official Soperator release tree contains an unsafe entry")
        entries.append(SoperatorGitTreeEntry(path, mode, entry_type, sha, size))
    return object_sha, tree_sha, tuple(entries)


def resolve_soperator_release(
    selector: str | None = "latest",
    *,
    current_release: str | None = None,
    opener: JsonOpener | None = None,
) -> SoperatorReleaseMetadata:
    """Resolve one stable official release without granting mutation authority."""

    normalized = normalize_soperator_release_selector(selector)
    if normalized == "latest":
        endpoint = f"{SOPERATOR_UPSTREAM_API}/releases/latest"
        release_payload = _json_request(
            endpoint,
            opener=opener,
            label=f"Soperator release {normalized}",
        )
    else:
        release_payload = _exact_release_payload(normalized, opener=opener)
    if release_payload.get("draft") is True or release_payload.get("prerelease") is True:
        raise ValueError("Soperator draft and prerelease builds are not supported")
    tag = str(release_payload.get("tag_name") or "").strip().removeprefix("v")
    release = str(SoperatorVersion.parse(tag))
    if normalized != "latest" and release != normalized:
        raise RuntimeError("official Soperator release tag does not match the requested version")
    if current_release:
        current = SoperatorVersion.parse(current_release)
        target = SoperatorVersion.parse(release)
        if target < current:
            raise ValueError(f"Soperator downgrade {current} -> {target} is not supported")
    commit, tree, entries = _git_ref_identity(release, opener=opener)
    archive_url = f"{SOPERATOR_UPSTREAM_REPOSITORY}/archive/refs/tags/{release}.tar.gz"
    return SoperatorReleaseMetadata(
        selector=normalized,
        release=release,
        repository=SOPERATOR_UPSTREAM_REPOSITORY,
        tag=release,
        commit=commit,
        tree=tree,
        archive_url=archive_url,
        archive_root=f"soperator-{release}",
        published_at=str(release_payload.get("published_at") or ""),
        tree_entries=entries,
    )


def verify_soperator_source_git_tree(
    source_root: Path,
    metadata: SoperatorReleaseMetadata,
) -> None:
    """Prove extracted regular files are the blobs of the resolved Git tree."""

    root = source_root.resolve(strict=True)
    expected = {
        entry.path: entry
        for entry in metadata.tree_entries
        if entry.object_type == "blob" and entry.mode != "120000"
    }
    unsupported = [
        entry.path
        for entry in metadata.tree_entries
        if entry.object_type in {"commit"} or entry.mode == "120000"
    ]
    if unsupported:
        raise ValueError(
            "Soperator release uses unsupported links or submodules: "
            + ", ".join(sorted(unsupported)[:5])
        )
    observed_paths = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if observed_paths != expected.keys():
        missing = sorted(expected.keys() - observed_paths)
        unexpected = sorted(observed_paths - expected.keys())
        raise ValueError(
            "Soperator source archive differs from its resolved Git tree "
            f"(missing={missing[:5]}, unexpected={unexpected[:5]})"
        )
    for relative, entry in expected.items():
        content = (root / relative).read_bytes()
        git_blob = hashlib.sha1(  # noqa: S324 - Git object identity is intentionally SHA-1.
            f"blob {len(content)}\0".encode() + content
        ).hexdigest()
        if git_blob != entry.sha or (entry.size is not None and len(content) != entry.size):
            raise ValueError(f"Soperator source blob differs from Git tree: {relative}")


def classify_soperator_release_capabilities(source_root: Path) -> tuple[str, str]:
    """Return a release-layout contract and deterministic capability fingerprint."""

    root = source_root.resolve(strict=True)
    chart_rows: list[tuple[str, str, str, str, str, str]] = []
    chart_directories: dict[str, Path] = {}
    for chart_file in sorted(root.glob("helm/*/Chart.yaml")):
        payload = yaml.safe_load(chart_file.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"invalid upstream Chart.yaml: {chart_file.relative_to(root)}")
        chart_name = str(payload.get("name") or "")
        chart_rows.append(
            (
                chart_file.parent.relative_to(root).as_posix(),
                chart_name,
                str(payload.get("apiVersion") or ""),
                str(payload.get("type") or "application"),
                str(payload.get("version") or ""),
                str(payload.get("appVersion") or ""),
            )
        )
        normalized_name = chart_name.removeprefix("helm-")
        if normalized_name in chart_directories:
            raise ValueError(
                "unknown Soperator release capability contract; duplicate normalized chart "
                f"name: {normalized_name}"
            )
        chart_directories[normalized_name] = chart_file.parent
    names = set(chart_directories)
    required_flux_files = {
        "helm/soperator-fluxcd/templates/soperator.yaml": (
            "apiVersion: helm.toolkit.fluxcd.io/v2",
            "kind: HelmRelease",
        ),
        "helm/soperator-fluxcd/templates/slurm-cluster.yaml": (
            "apiVersion: helm.toolkit.fluxcd.io/v2",
            "kind: HelmRelease",
        ),
        "helm/slurm-cluster/templates/slurm-cluster-cr.yaml": (
            "apiVersion: slurm.nebius.ai/v1",
            "kind: SlurmCluster",
        ),
        "helm/soperator/crds/slurmcluster-crd.yaml": (
            "name: slurmclusters.slurm.nebius.ai",
            "group: slurm.nebius.ai",
        ),
    }
    structural_rows: list[tuple[str, str]] = []

    def _require_flux_structure() -> None:
        for relative, markers in required_flux_files.items():
            path = root / relative
            if not path.is_file():
                raise ValueError(
                    "unknown Soperator release capability contract; required upstream "
                    f"structure is missing: {relative}"
                )
            content = path.read_text(encoding="utf-8", errors="strict")
            if any(marker not in content for marker in markers):
                raise ValueError(
                    "unknown Soperator release capability contract; required upstream "
                    f"structure is incompatible: {relative}"
                )
            structural_rows.append(
                (relative, "sha256:" + hashlib.sha256(content.encode()).hexdigest())
            )
        values_path = root / "helm/soperator-fluxcd/values.yaml"
        if not values_path.is_file():
            raise ValueError(
                "unknown Soperator release capability contract; required upstream "
                "structure is missing: helm/soperator-fluxcd/values.yaml"
            )
        values = yaml.safe_load(values_path.read_text(encoding="utf-8"))
        required_values = {"observability", "slurmCluster", "soperator"}
        if not isinstance(values, Mapping) or not required_values.issubset(values):
            raise ValueError(
                "unknown Soperator release capability contract; the upstream Flux values "
                "contract is incomplete"
            )
        structural_rows.append(
            (
                "helm/soperator-fluxcd/values.yaml",
                "sha256:" + hashlib.sha256(values_path.read_bytes()).hexdigest(),
            )
        )

    def _require_core_structure() -> None:
        if not {"soperator", "slurm-cluster"}.issubset(chart_directories):
            raise ValueError(
                "unknown Soperator release capability contract; both operator and "
                "SlurmCluster charts are required"
            )
        core_files = {
            chart_directories["slurm-cluster"] / "templates" / "slurm-cluster-cr.yaml": (
                "apiVersion: slurm.nebius.ai/v1",
                "kind: SlurmCluster",
            ),
            chart_directories["soperator"] / "crds" / "slurmcluster-crd.yaml": (
                "name: slurmclusters.slurm.nebius.ai",
                "group: slurm.nebius.ai",
            ),
        }
        for path, markers in core_files.items():
            relative = path.relative_to(root).as_posix()
            if not path.is_file():
                raise ValueError(
                    "unknown Soperator release capability contract; required protected "
                    f"data-plane structure is missing: {relative}"
                )
            content = path.read_text(encoding="utf-8", errors="strict")
            if any(marker not in content for marker in markers):
                raise ValueError(
                    "unknown Soperator release capability contract; required protected "
                    f"data-plane structure is incompatible: {relative}"
                )
            structural_rows.append(
                (relative, "sha256:" + hashlib.sha256(content.encode()).hexdigest())
            )
        values_path = chart_directories["slurm-cluster"] / "values.yaml"
        if not values_path.is_file():
            raise ValueError(
                "unknown Soperator release capability contract; the protected "
                "data-plane values contract is missing"
            )
        values = yaml.safe_load(values_path.read_text(encoding="utf-8"))
        required_values = {"images", "populateJail", "slurmNodes", "volumeSources"}
        if not isinstance(values, Mapping) or not required_values.issubset(values):
            raise ValueError(
                "unknown Soperator release capability contract; the protected "
                "data-plane values contract is incomplete"
            )
        relative = values_path.relative_to(root).as_posix()
        structural_rows.append(
            (relative, "sha256:" + hashlib.sha256(values_path.read_bytes()).hexdigest())
        )

    required_upstream_names = {
        chart_name.removeprefix("helm-") for chart_name, _role in SOPERATOR_UPSTREAM_CHART_ROLES
    }
    if required_upstream_names.issubset(names):
        _require_flux_structure()
        _require_core_structure()
        contract = "upstream-flux-v1"
    elif {"soperator", "slurm-cluster"}.issubset(names):
        modern_markers = {"soperator-fluxcd-bootstrap", "nodesets"}
        if names & modern_markers:
            missing = sorted(required_upstream_names - names)
            raise ValueError(
                "unknown Soperator release capability contract; the upstream Flux "
                f"chart graph is incomplete (missing={missing})"
            )
        _require_core_structure()
        contract = "protected-data-plane-v1"
    else:
        raise ValueError("unknown Soperator release capability contract; no mutation is permitted")
    capability_material = {
        "contract": contract,
        "charts": chart_rows,
        "requiredStructure": structural_rows,
        "hasStorageChart": "slurm-cluster-storage" in names,
        "hasActiveChecks": "soperator-activechecks" in names,
    }
    encoded = json.dumps(capability_material, sort_keys=True, separators=(",", ":")).encode()
    return contract, f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def seal_soperator_release_snapshot(
    snapshot: SoperatorReleaseSnapshot,
) -> SoperatorReleaseSnapshot:
    """Validate and seal a fully resolved operation snapshot."""

    if snapshot.schema != SOPERATOR_RELEASE_SNAPSHOT_SCHEMA:
        raise ValueError(f"Soperator snapshot schema must be {SOPERATOR_RELEASE_SNAPSHOT_SCHEMA}")
    normalize_soperator_release_selector(snapshot.release)
    if snapshot.repository != SOPERATOR_UPSTREAM_REPOSITORY:
        raise ValueError("Soperator snapshot repository is not the official upstream")
    if snapshot.tag != snapshot.release:
        raise ValueError("Soperator snapshot tag must equal its stable release")
    if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", snapshot.populate_jail_image):
        raise ValueError("Soperator snapshot populate-jail image must be digest-addressed")
    if not _GIT_OBJECT_RE.fullmatch(snapshot.commit) or not _GIT_OBJECT_RE.fullmatch(snapshot.tree):
        raise ValueError("Soperator snapshot requires exact commit and tree identities")
    for label, digest in (
        ("archive", snapshot.archive_sha256),
        ("source manifest", snapshot.source_manifest_sha256),
        ("scripts manifest", snapshot.scripts_manifest_sha256),
        ("capability", snapshot.capability_sha256),
    ):
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError(f"Soperator snapshot {label} must use an exact SHA-256")
    if not snapshot.charts or "umbrella" not in snapshot.charts:
        raise ValueError("Soperator snapshot has no verified upstream umbrella chart")
    if not snapshot.release_graph:
        raise ValueError("Soperator snapshot has no verified release graph")
    for key, chart in snapshot.charts.items():
        if not chart.version:
            raise ValueError(f"Soperator upstream chart {key} has no exact version")
        if not all(
            _SHA256_RE.fullmatch(value)
            for value in (
                chart.digest,
                chart.package_sha256,
                chart.source_tree_sha256,
            )
        ):
            raise ValueError(f"Soperator upstream chart {key} lacks immutable digests")
        if (
            not _RELATIVE_PATH_RE.fullmatch(chart.source_path)
            or chart.source_path.startswith("/")
            or any(part in {"", ".", ".."} for part in chart.source_path.split("/"))
        ):
            raise ValueError(f"Soperator upstream chart {key} has an unsafe source path")
    for key, third_party_chart in snapshot.third_party_charts.items():
        if not _SHA256_RE.fullmatch(third_party_chart.package_sha256):
            raise ValueError(f"Soperator third-party chart {key} lacks a package digest")
        if third_party_chart.oci_digest is not None and not _SHA256_RE.fullmatch(
            third_party_chart.oci_digest
        ):
            raise ValueError(f"Soperator third-party OCI chart {key} lacks a manifest digest")
        if not third_party_chart.repository.startswith(("https://", "oci://")):
            raise ValueError(f"Soperator third-party chart {key} has an invalid repository")
    names = {node.release_name for node in snapshot.release_graph}
    if len(names) != len(snapshot.release_graph):
        raise ValueError("Soperator snapshot release graph contains duplicate names")
    main_nodes = [node for node in snapshot.release_graph if node.is_main]
    if len(main_nodes) != 1 or main_nodes[0].release_name != SOPERATOR_MAIN_RELEASE_NAME:
        raise ValueError(
            "Soperator snapshot release graph must declare the Slurm cluster release as its "
            "one main workload"
        )
    for node in snapshot.release_graph:
        inventory: Mapping[str, object] = (
            snapshot.charts if node.owner == "upstream" else snapshot.third_party_charts
        )
        if node.owner not in {"upstream", "third-party"} or node.chart_key not in inventory:
            raise ValueError(f"Soperator release graph node {node.release_name} has no artifact")
        if set(node.dependencies) - names:
            raise ValueError(
                f"Soperator release graph node {node.release_name} has unknown dependencies"
            )
    canonical = snapshot.canonical_payload(include_digest=False)
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    if snapshot.snapshot_sha256 and snapshot.snapshot_sha256 != digest:
        raise ValueError("Soperator release snapshot digest does not match its content")
    return replace(snapshot, snapshot_sha256=digest)


def load_soperator_release_snapshot(path: Path) -> SoperatorReleaseSnapshot:
    payload = read_owner_only_json(path, label="Soperator release snapshot")
    if not isinstance(payload, Mapping):
        raise ValueError("Soperator release snapshot must be a JSON object")
    charts_payload = payload.get("charts")
    third_party_payload = payload.get("third_party_charts")
    graph_payload = payload.get("release_graph")
    if not isinstance(charts_payload, Mapping) or not isinstance(third_party_payload, Mapping):
        raise ValueError("Soperator release snapshot artifact inventories are invalid")
    if not isinstance(graph_payload, Sequence) or isinstance(graph_payload, (str, bytes)):
        raise ValueError("Soperator release snapshot graph is invalid")
    if any(not isinstance(value, Mapping) for value in charts_payload.values()):
        raise ValueError("Soperator release snapshot upstream chart inventory is invalid")
    if any(not isinstance(value, Mapping) for value in third_party_payload.values()):
        raise ValueError("Soperator release snapshot third-party chart inventory is invalid")
    if any(not isinstance(value, Mapping) for value in graph_payload):
        raise ValueError("Soperator release snapshot graph contains an invalid node")
    snapshot = SoperatorReleaseSnapshot(
        schema=str(payload.get("schema") or ""),
        selector=str(payload.get("selector") or ""),
        release=str(payload.get("release") or ""),
        repository=str(payload.get("repository") or ""),
        tag=str(payload.get("tag") or ""),
        commit=str(payload.get("commit") or ""),
        tree=str(payload.get("tree") or ""),
        archive_url=str(payload.get("archive_url") or ""),
        archive_sha256=str(payload.get("archive_sha256") or ""),
        archive_root=str(payload.get("archive_root") or ""),
        source_manifest_sha256=str(payload.get("source_manifest_sha256") or ""),
        registry=str(payload.get("registry") or ""),
        capability_contract=str(payload.get("capability_contract") or ""),
        capability_sha256=str(payload.get("capability_sha256") or ""),
        charts={
            str(key): SoperatorChartSnapshot(**dict(value)) for key, value in charts_payload.items()
        },
        third_party_charts={
            str(key): SoperatorThirdPartyChartSnapshot(**dict(value))
            for key, value in third_party_payload.items()
        },
        release_graph=tuple(
            SoperatorReleaseGraphNode(
                release_name=str(value.get("release_name") or ""),
                namespace=str(value.get("namespace") or ""),
                owner=str(value.get("owner") or ""),
                stage=int(value.get("stage") or 0),
                chart_key=str(value.get("chart_key") or ""),
                dependencies=tuple(str(item) for item in value.get("dependencies") or ()),
                is_main=value.get("is_main") is True,
            )
            for value in graph_payload
        ),
        scripts_manifest_sha256=str(payload.get("scripts_manifest_sha256") or ""),
        image_references=tuple(str(value) for value in payload.get("image_references") or ()),
        mount_image=str(payload.get("mount_image") or ""),
        adapter_state_schema=str(payload.get("adapter_state_schema") or ""),
        populate_jail_image=str(payload.get("populate_jail_image") or ""),
        jail_cuda_version=str(payload.get("jail_cuda_version") or ""),
        snapshot_sha256=str(payload.get("snapshot_sha256") or ""),
    )
    return seal_soperator_release_snapshot(snapshot)


def write_soperator_release_snapshot(path: Path, snapshot: SoperatorReleaseSnapshot) -> None:
    sealed = seal_soperator_release_snapshot(snapshot)
    write_owner_only_json(path, sealed.canonical_payload())


def soperator_release_snapshot_path(reports_dir: Path, target_ref: str) -> Path:
    token = re.sub(r"[^A-Za-z0-9.-]+", "-", str(target_ref or "default")).strip("-.")
    if not token or token in {".", ".."}:
        raise ValueError("Soperator target does not produce a safe snapshot path")
    return reports_dir / f"soperator-release-snapshot-{token}.json"


__all__ = [
    "SOPERATOR_ADAPTER_MOUNT_IMAGE",
    "SOPERATOR_RELEASE_SNAPSHOT_SCHEMA",
    "SOPERATOR_MAIN_RELEASE_NAME",
    "SOPERATOR_UPSTREAM_API",
    "SOPERATOR_UPSTREAM_CHART_ROLES",
    "SOPERATOR_UPSTREAM_REGISTRY",
    "SOPERATOR_UPSTREAM_REPOSITORY",
    "SOPERATOR_UPSTREAM_UMBRELLA_CHART",
    "SoperatorChartSnapshot",
    "SoperatorGitTreeEntry",
    "SoperatorReleaseGraphNode",
    "SoperatorReleaseMetadata",
    "SoperatorReleaseSnapshot",
    "SoperatorThirdPartyChartSnapshot",
    "SoperatorVersion",
    "classify_soperator_release_capabilities",
    "load_soperator_release_snapshot",
    "normalize_soperator_release_selector",
    "resolve_soperator_release",
    "seal_soperator_release_snapshot",
    "soperator_release_snapshot_path",
    "verify_soperator_source_git_tree",
    "write_soperator_release_snapshot",
]
