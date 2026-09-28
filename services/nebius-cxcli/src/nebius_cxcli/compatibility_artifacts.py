"""Content-bound chart inputs reused by rendering and compatibility admission."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from .compatibility_matrix import digest

_INPUTS: ContextVar[Mapping | None] = ContextVar("compatibility_chart_inputs", default=None)


def reference_key(reference) -> str:
    return digest(asdict(reference))


def capture_chart(reference, directory: Path, *, oci_digest: str | None = None) -> dict:
    files: dict = {}
    total = 0
    for path in sorted(directory.rglob("*")):
        if ".git" in path.relative_to(directory).parts:
            continue
        if path.is_symlink():
            raise ValueError("Chart input contains a symlink")
        if path.is_file():
            data = path.read_bytes()
            total += len(data)
            if total > 40 * 1024 * 1024 or len(files) >= 10000:
                raise ValueError("Chart input exceeds snapshot bounds")
            files[path.relative_to(directory).as_posix()] = base64.b64encode(data).decode("ascii")
    body = {"reference": asdict(reference), "files": files, "oci_digest": oci_digest}
    return {**body, "sha256": digest(body)}


def verify_chart(value: Mapping) -> None:
    if (
        set(value) != {"reference", "files", "oci_digest", "sha256"}
        or len(json.dumps(value)) > 56 * 1024 * 1024
    ):
        raise ValueError("Malformed frozen chart input")
    if value["sha256"] != digest({k: v for k, v in value.items() if k != "sha256"}):
        raise ValueError("Frozen chart integrity mismatch")
    if (
        not isinstance(value["reference"], Mapping)
        or set(value["reference"]) != {"chart_name", "chart_repo", "chart_version"}
        or any(not isinstance(v, str) for v in value["reference"].values())
    ):
        raise ValueError("Malformed frozen chart reference")
    if value["oci_digest"] is not None and not re.fullmatch(
        r"sha256:[0-9a-f]{64}", value["oci_digest"]
    ):
        raise ValueError("Malformed chart OCI digest")
    if "Chart.yaml" not in value["files"] or len(value["files"]) > 10000:
        raise ValueError("Frozen chart metadata is missing")
    total = 0
    for name, encoded in value["files"].items():
        path = PurePosixPath(name)
        if (
            not name
            or "\x00" in name
            or path.is_absolute()
            or str(path) != name
            or any(p in {".", ".."} for p in path.parts)
            or "\\" in name
            or ".git" in path.parts
        ):
            raise ValueError("Unsafe frozen chart path")
        total += len(base64.b64decode(encoded, validate=True))
    if total > 40 * 1024 * 1024:
        raise ValueError("Frozen chart input exceeds snapshot bounds")


@contextmanager
def frozen_chart_inputs(inputs: Mapping) -> Iterator[None]:
    for key, value in inputs.items():
        verify_chart(value)
        if key != digest(value["reference"]):
            raise ValueError("Frozen chart reference changed")
    token = _INPUTS.set(inputs)
    try:
        yield
    finally:
        _INPUTS.reset(token)


def frozen_chart(reference) -> Mapping | None:
    inputs = _INPUTS.get()
    if inputs is None:
        return None
    value = inputs.get(reference_key(reference))
    if value is None:
        raise ValueError("Selected chart is absent from the frozen generation; rerender required")
    return value


def frozen_local_chart(chart_path: str) -> Mapping | None:
    inputs = _INPUTS.get()
    if inputs is None:
        return None
    matches = [
        value
        for value in inputs.values()
        if value["reference"]["chart_name"] == chart_path and not value["reference"]["chart_repo"]
    ]
    if len(matches) != 1:
        raise ValueError("Local chart is absent or ambiguous in the frozen generation")
    return matches[0]


@contextmanager
def materialize_frozen_chart(value: Mapping) -> Iterator[Path]:
    verify_chart(value)
    with tempfile.TemporaryDirectory(prefix="cxcli-frozen-chart-") as directory:
        root = Path(directory) / "chart"
        root.mkdir()
        for name, encoded in value["files"].items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(encoded, validate=True))
            path.chmod(0o600)
        yield root


def tree_digest(value: Mapping) -> str:
    return digest(
        {
            name: hashlib.sha256(base64.b64decode(data, validate=True)).hexdigest()
            for name, data in value["files"].items()
        }
    )


def _require_http_chart_identity(value: Mapping) -> None:
    import yaml

    from .helm_chart_versions import exact_helm_chart_version

    reference = value["reference"]
    version = exact_helm_chart_version(reference["chart_version"])
    try:
        metadata = yaml.safe_load(base64.b64decode(value["files"]["Chart.yaml"], validate=True))
    except (ValueError, yaml.YAMLError):
        raise ValueError("HTTP chart metadata is malformed") from None
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("name") != reference["chart_name"]
        or not isinstance(metadata.get("version"), str)
        or metadata["version"].removeprefix("v") != version.removeprefix("v")
        or value["oci_digest"] is not None
    ):
        raise ValueError("HTTP chart identity differs from its exact selected name/version")


def admit_chart_sources(inputs: Mapping) -> None:
    """Check HTTP source freshness; retain OCI pinning and local frozen replay.

    Native Flux HTTP execution trusts the repository after this check. This is
    deliberately not described as an immutable execution binding.
    """
    import yaml

    from .helm_client import HelmChartReference, _github_tree_ref, _is_http_repo

    for value in inputs.values():
        verify_chart(value)
        reference = HelmChartReference(**value["reference"])
        repository = reference.chart_repo.strip()
        if not repository:
            if (
                urlsplit(reference.chart_name).scheme
                or _github_tree_ref(reference.chart_name) is not None
            ):
                raise ValueError("Remote chart source requires an explicit repository")
            continue
        if repository.startswith("oci://"):
            if not value["oci_digest"]:
                raise ValueError("OCI chart has no immutable execution binding; rerender required")
            continue
        if (
            not _is_http_repo(repository)
            or _github_tree_ref(repository) is not None
            or _github_tree_ref(reference.chart_name) is not None
        ):
            raise ValueError(
                "Chart source execution requires an HTTP Helm repository or OCI source"
            )
        _require_http_chart_identity(value)
        # Admission often runs inside nested frozen contexts. A replay of the
        # snapshot cannot establish whether the publisher changed this version.
        token = _INPUTS.set(None)
        try:
            fresh = resolve_chart_input(reference)
        except (OSError, RuntimeError, ValueError, yaml.YAMLError):
            raise ValueError(
                "Cannot refresh the selected HTTP chart; check repository access before deployment"
            ) from None
        finally:
            _INPUTS.reset(token)
        verify_chart(fresh)
        _require_http_chart_identity(fresh)
        if fresh["reference"] != value["reference"] or tree_digest(fresh) != tree_digest(value):
            raise ValueError(
                "HTTP chart contents changed since render; rerender and review before deployment"
            )


def resolve_chart_input(reference) -> dict:
    frozen = frozen_chart(reference)
    if frozen is not None:
        return dict(frozen)
    from .helm_client import _materialize_chart_dir

    repository = reference.chart_repo.rstrip("/")
    if repository.startswith("oci://"):
        from .soperator_release_artifacts import _chart_file_map
        from .soperator_release_resolver import _downloaded_chart

        chart = reference.chart_name
        if repository.rsplit("/", 1)[-1] == chart:
            repository = repository.rsplit("/", 1)[0]
        with (
            tempfile.TemporaryDirectory(prefix="cxcli-chart-package-") as directory,
            _downloaded_chart(
                helm="helm",
                chart=chart,
                version=reference.chart_version,
                repository=repository,
                destination=Path(directory),
            ) as (root, metadata),
        ):
            _, _, oci_digest = metadata
            files = _chart_file_map(next(root.glob("*.tgz")))
            body = {
                "reference": asdict(reference),
                "files": {
                    name: base64.b64encode(data).decode("ascii") for name, data in files.items()
                },
                "oci_digest": oci_digest,
            }
            value = {**body, "sha256": digest(body)}
            verify_chart(value)
            return value
    with _materialize_chart_dir(reference) as directory:
        if not reference.chart_repo:
            from .flux_render import _build_local_helm_chart_dependencies, _stage_local_helm_chart

            with tempfile.TemporaryDirectory(prefix="cxcli-local-chart-input-") as staging:
                staged = _stage_local_helm_chart(str(directory), Path(staging))
                _build_local_helm_chart_dependencies(staged)
                return capture_chart(reference, Path(staged))
        return capture_chart(reference, directory)


def bind_flux_artifacts(paths, inputs: Mapping) -> None:
    """Pin ordinary OCI chart execution to the same manifest assessed at render."""
    import yaml

    from .flux_render import _oci_repository_doc

    files: dict = {}
    sources = {}
    for path in sorted(paths.flux_dir.rglob("*.yaml")):
        documents = list(yaml.safe_load_all(path.read_text()))
        files[path] = documents
        for doc in documents:
            if isinstance(doc, dict) and doc.get("kind") == "HelmRepository":
                sources[(path.parent, doc["metadata"]["name"])] = doc
    source_urls = {key: source["spec"]["url"].rstrip("/") for key, source in sources.items()}
    selected: dict = {}
    for path, documents in files.items():
        changed = False
        for doc in documents:
            if (
                not isinstance(doc, dict)
                or doc.get("kind") != "HelmRelease"
                or "chart" not in doc.get("spec", {})
            ):
                continue
            chart = doc["spec"]["chart"].get("spec", {})
            source = chart.get("sourceRef", {})
            if source.get("kind") != "HelmRepository":
                continue
            key = (path.parent, source.get("name"))
            repository = sources.get(key)
            if repository is None or (
                repository.get("spec", {}).get("type") != "oci" and key not in selected
            ):
                continue
            # HelmRepository owns a registry parent; OCIRepository owns the
            # complete artifact repository, including the release's chart name.
            url = source_urls[key] + "/" + chart["chart"].strip("/")
            matches = []
            for value in inputs.values():
                ref = value["reference"]
                candidate = ref["chart_repo"].rstrip("/")
                if candidate.rsplit("/", 1)[-1] != ref["chart_name"]:
                    candidate += "/" + ref["chart_name"]
                if candidate == url and ref["chart_version"] == chart.get("version"):
                    matches.append(value)
            if len(matches) != 1 or not matches[0]["oci_digest"]:
                raise ValueError("Rendered OCI release has no exact frozen chart identity")
            digest_value = matches[0]["oci_digest"]
            if key in selected and selected[key] != digest_value:
                raise ValueError("One chart source cannot own different immutable releases")
            selected[key] = digest_value
            replacement = _oci_repository_doc(source["name"], url, digest=digest_value)
            replacement["metadata"].update(copy.deepcopy(repository["metadata"]))
            repository.clear()
            repository.update(replacement)
            doc["spec"].pop("chart")
            doc["spec"]["chartRef"] = {**source, "kind": "OCIRepository"}
            changed = True
        if changed:
            path.write_text(yaml.safe_dump_all(documents, sort_keys=False))
    # Sources and HelmReleases normally reside in separate renderer-owned files.
    for path, documents in files.items():
        if any(
            isinstance(doc, Mapping)
            and (path.parent, doc.get("metadata", {}).get("name")) in selected
            and doc.get("kind") == "OCIRepository"
            for doc in documents
        ):
            path.write_text(yaml.safe_dump_all(documents, sort_keys=False))
