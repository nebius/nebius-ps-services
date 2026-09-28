from __future__ import annotations

import io
import os
import subprocess
import tarfile
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
import yaml

import nebius_cxcli.cli as cli
import nebius_cxcli.soperator_release_resolver as resolver
from nebius_cxcli.soperator_release import SoperatorArtifactRequest
from soperator_fixtures import sample_snapshot


def _write_chart_package(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, mode="w:gz") as package:
        for name, payload in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            package.addfile(member, io.BytesIO(payload))


def _chart_limits(**overrides: int) -> resolver._ChartPackageLimits:
    values = {
        "max_compressed_bytes": 1024 * 1024,
        "max_members": 10,
        "max_member_bytes": 1024,
        "max_expanded_bytes": 2048,
        "max_metadata_bytes": 512,
    }
    values.update(overrides)
    return resolver._ChartPackageLimits(**values)


def _snapshot_with_current_mount_image():
    return replace(
        sample_snapshot(),
        mount_image=resolver.SOPERATOR_ADAPTER_MOUNT_IMAGE,
        snapshot_sha256="",
    )


@pytest.mark.parametrize("origin", ["fresh", "recent", "frozen"])
@pytest.mark.parametrize("valid", [False, True])
def test_release_source_verification_precedes_selection_publication(
    tmp_path, monkeypatch, origin, valid
):
    from nebius_cxcli import soperator_release_artifacts
    from test_soperator_release_identity import _metadata

    snapshot = resolver.seal_soperator_release_snapshot(_snapshot_with_current_mount_image())
    if origin == "frozen":
        resolver._retain_release_snapshot(snapshot, cache_root=tmp_path)
    source = SimpleNamespace(manifest_sha256=snapshot.source_manifest_sha256)
    metadata = _metadata()
    described = SimpleNamespace(
        source=source,
        metadata=metadata,
        release=snapshot.release,
        identity_sha256=snapshot.source_manifest_sha256,
    )
    monkeypatch.setattr(
        "nebius_cxcli.soperator_artifact_selection.compile_required_stages",
        lambda *a: {"desired": ({}, [])},
    )
    writes = []
    monkeypatch.setattr(
        "nebius_cxcli.soperator_adapter.render_soperator_adapter_documents",
        lambda *a, **k: ([], {}),
    )
    monkeypatch.setattr(
        "nebius_cxcli.soperator_values.with_source_observability", lambda values, source: values
    )

    class Ledger:
        def __init__(self, _root):
            pass

        def locked(self, _metadata):
            return nullcontext("identity")

        def record(self, _identity):
            writes.append("identity")

    monkeypatch.setattr(resolver, "SoperatorReleaseIdentityLedger", Ledger)
    monkeypatch.setattr(
        resolver,
        "_load_recent_release_snapshot",
        lambda *a, **k: snapshot if origin == "recent" else None,
    )
    monkeypatch.setattr(resolver, "resolve_soperator_release", lambda *a, **k: metadata)
    monkeypatch.setattr(resolver, "acquire_soperator_release_source", lambda *a, **k: source)
    monkeypatch.setattr(
        resolver,
        "build_soperator_release_snapshot",
        lambda *a, **k: (verify(snapshot, source), snapshot)[1],
    )
    monkeypatch.setattr(
        resolver, "_retain_release_snapshot", lambda *a, **k: writes.append("retain")
    )
    monkeypatch.setattr(
        resolver, "_write_recent_release_snapshot", lambda *a, **k: writes.append("cache")
    )
    monkeypatch.setattr(
        resolver,
        "frozen_soperator_release_from_snapshot",
        lambda *a, **k: resolver.FrozenSoperatorRelease(metadata, source, snapshot),
    )

    def verify(*args, **kwargs):
        assert args == (snapshot, source)
        if not valid:
            raise ValueError("official OCI chart helm-nfs-server differs from release source")
        writes.append("verified")

    monkeypatch.setattr(soperator_release_artifacts, "verify_soperator_release_artifacts", verify)

    def freeze():
        return resolver.freeze_soperator_release(
            "4.1.7",
            cache_root=tmp_path,
            target_ref=snapshot.target_ref,
            source=described,
            request=SoperatorArtifactRequest.deployment(snapshot.target_ref, {}),
            snapshot_sha256=snapshot.snapshot_sha256 if origin == "frozen" else None,
        )

    if valid:
        assert freeze().snapshot == snapshot
        assert writes == (
            ["verified", "retain"] if origin == "frozen" else ["verified", "identity", "cache"]
        )
    else:
        with pytest.raises(ValueError, match="helm-nfs-server differs from release source"):
            freeze()
        assert not writes


def test_release_command_failure_is_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver.kubernetes_process,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                "download failed at https://private.example.invalid/chart "
                '{"access_token":"release-sensitive-value"}\n' + "x" * 5000
            ),
        ),
    )

    with pytest.raises(RuntimeError) as excinfo:
        resolver._run(["helm", "pull"], label="download official chart")

    detail = str(excinfo.value)
    assert "exit code 1" in detail
    assert "private.example.invalid" not in detail
    assert "release-sensitive-value" not in detail
    assert "<url>" in detail
    assert "<redacted>" in detail
    assert len(detail) < 2200


def test_release_command_timeout_is_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _timeout(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise subprocess.TimeoutExpired(
            ["helm", "pull"],
            300,
            stderr=b"Authorization: Bearer release-timeout-sensitive-value",
        )

    monkeypatch.setattr(resolver.kubernetes_process, "run", _timeout)

    with pytest.raises(RuntimeError, match="timed out after 300 seconds") as excinfo:
        resolver._run(["helm", "pull"], label="download official chart")

    assert "release-timeout-sensitive-value" not in str(excinfo.value)
    assert "<redacted>" in str(excinfo.value)


def test_recent_release_snapshot_cache_is_request_bound_and_expires(tmp_path: Path) -> None:
    snapshot = _snapshot_with_current_mount_image()
    cache_root = tmp_path / "cache"
    path = resolver._write_recent_release_snapshot(
        snapshot,
        cache_root=cache_root,
    )
    written_at = path.stat().st_mtime

    cached = resolver._load_recent_release_snapshot(
        snapshot.request_sha256,
        cache_root=cache_root,
        now=written_at + 899,
    )

    assert cached is not None
    assert cached.selector == "4.1.7"
    assert cached.release == "4.1.7"
    assert (
        resolver._load_recent_release_snapshot(
            snapshot.request_sha256,
            cache_root=cache_root,
            now=written_at + 901,
        )
        is None
    )


def test_fresh_admission_requires_explicit_target_request(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "resolve_soperator_release",
        lambda *a, **k: pytest.fail("no discovery without a request"),
    )
    with pytest.raises(ValueError, match="target configuration request"):
        resolver.freeze_soperator_release("4.1.7")


def test_recent_release_snapshot_rejects_stale_adapter_mount_image(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    stale = replace(
        sample_snapshot(),
        mount_image="registry.example.invalid/mount@sha256:" + "0" * 64,
        snapshot_sha256="",
    )
    path = resolver._write_recent_release_snapshot(
        stale,
        cache_root=cache_root,
    )

    assert (
        resolver._load_recent_release_snapshot(
            stale.request_sha256,
            cache_root=cache_root,
            now=path.stat().st_mtime + 1,
        )
        is None
    )


@pytest.mark.parametrize("cached_selector", ["latest", "4.1.7"])
@pytest.mark.parametrize("admitted_selector", ["latest", "4.1.7"])
def test_frozen_digest_survives_selector_expiry_and_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cached_selector: str, admitted_selector: str
) -> None:
    original = resolver.seal_soperator_release_snapshot(
        replace(_snapshot_with_current_mount_image(), selector=admitted_selector)
    )
    cache_root = tmp_path / "cache"
    resolver._write_recent_release_snapshot(original, cache_root=cache_root)
    path = resolver._write_recent_release_snapshot(
        replace(original, selector=cached_selector, snapshot_sha256=""), cache_root=cache_root
    )
    os.utime(path, (1, 1))
    changed = replace(original, archive_sha256="sha256:" + "a" * 64, snapshot_sha256="")
    resolver._write_recent_release_snapshot(changed, cache_root=cache_root)
    observed = []

    class _Ledger:
        def __init__(self, _root):
            pass

        def locked(self, metadata):
            observed.append(metadata)
            return nullcontext()

    def rehydrate(snapshot, **_kwargs):
        assert snapshot == original
        return SimpleNamespace(metadata="verified", snapshot=snapshot, source=object())

    monkeypatch.setattr(resolver, "SoperatorReleaseIdentityLedger", _Ledger)
    monkeypatch.setattr(
        "nebius_cxcli.soperator_release_artifacts.verify_soperator_release_artifacts",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(resolver, "frozen_soperator_release_from_snapshot", rehydrate)
    monkeypatch.setattr(
        resolver,
        "resolve_soperator_release",
        lambda *_a, **_k: pytest.fail("frozen authority must not resolve mutable tags"),
    )
    frozen = resolver.freeze_soperator_release(
        "4.1.7",
        target_ref=original.target_ref,
        cache_root=cache_root,
        snapshot_sha256=original.snapshot_sha256,
    )
    assert frozen.snapshot == original
    assert observed == ["verified"]


@pytest.mark.parametrize("digest", ["bad", "sha256:" + "f" * 64])
def test_frozen_digest_missing_or_invalid_never_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, digest: str
) -> None:
    monkeypatch.setattr(
        resolver,
        "resolve_soperator_release",
        lambda *_a, **_k: pytest.fail("missing frozen content must stop"),
    )
    with pytest.raises((ValueError, RuntimeError), match="snapshot"):
        resolver.freeze_soperator_release(
            "4.1.7", target_ref="soperator", cache_root=tmp_path, snapshot_sha256=digest
        )


@pytest.mark.parametrize("retained", [True, False])
def test_frozen_latest_digest_survives_exact_release_handoff(
    tmp_path: Path,
    retained: bool,
) -> None:
    original = resolver.seal_soperator_release_snapshot(
        replace(_snapshot_with_current_mount_image(), selector="latest")
    )
    path = resolver._recent_release_snapshot_path(original.request_sha256, cache_root=tmp_path)
    resolver.write_soperator_release_snapshot(path, original)
    if retained:
        resolver._retain_release_snapshot(original, cache_root=tmp_path)
    os.utime(path, (1, 1))
    if not retained:
        with pytest.raises((ValueError, RuntimeError), match="snapshot"):
            resolver._load_frozen_release_snapshot(
                "4.1.7",
                original.snapshot_sha256,
                target_ref=original.target_ref,
                cache_root=tmp_path,
            )
        return
    assert (
        resolver._load_frozen_release_snapshot(
            "4.1.7", original.snapshot_sha256, target_ref=original.target_ref, cache_root=tmp_path
        )
        == original
    )
    with pytest.raises(ValueError, match="snapshot identity differs"):
        resolver._load_frozen_release_snapshot(
            "4.1.5", original.snapshot_sha256, target_ref=original.target_ref, cache_root=tmp_path
        )


@pytest.mark.parametrize("corruption", ["symlink", "different-content", "changed-digest"])
def test_retained_snapshot_corruption_stops_before_selector_lookup(
    tmp_path: Path, corruption: str
) -> None:
    original = resolver.seal_soperator_release_snapshot(_snapshot_with_current_mount_image())
    selector = resolver._write_recent_release_snapshot(original, cache_root=tmp_path)
    path = selector.parent / "by-digest" / f"{original.snapshot_sha256[7:]}.json"
    if corruption == "symlink":
        path.unlink()
        path.symlink_to(selector)
    else:
        changed = replace(original, archive_sha256="sha256:" + "a" * 64, snapshot_sha256="")
        resolver.write_soperator_release_snapshot(
            path, resolver.seal_soperator_release_snapshot(changed)
        )
        if corruption == "changed-digest":
            path.write_text(
                path.read_text().replace(changed.archive_sha256, original.archive_sha256)
            )
    with pytest.raises((ValueError, RuntimeError)):
        resolver._load_frozen_release_snapshot(
            "4.1.7", original.snapshot_sha256, target_ref=original.target_ref, cache_root=tmp_path
        )


@pytest.mark.parametrize(
    "repository",
    (
        "http://charts.example.invalid",
        "file:///tmp/charts",
        "https://user:password@charts.example.invalid",
        "https://charts.example.invalid:444",
        "https://charts.example.invalid/path?token=value",
        "https://charts.example.invalid/path#fragment",
        "oci://",
        "//charts.example.invalid/path",
    ),
)
def test_chart_repository_validation_rejects_unsafe_transport_before_helm(
    repository: str,
) -> None:
    with pytest.raises(ValueError, match="repository"):
        resolver._validated_chart_repository(repository)


@pytest.mark.parametrize(
    ("repository", "expected"),
    (
        ("https://charts.example.invalid/path/", "https://charts.example.invalid/path"),
        ("oci://registry.example.invalid/charts/", "oci://registry.example.invalid/charts"),
    ),
)
def test_chart_repository_validation_accepts_only_canonical_secure_urls(
    repository: str,
    expected: str,
) -> None:
    assert resolver._validated_chart_repository(repository) == expected


def test_chart_pull_retries_transient_timeout_in_clean_isolated_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[Path] = []
    delays: list[float] = []

    def _pull_once(**kwargs):
        destination = kwargs["destination"]
        attempts.append(destination)
        (destination / "partial.tgz").write_bytes(b"partial")
        if len(attempts) == 1:
            raise subprocess.TimeoutExpired(["helm", "pull"], timeout=300)
        return "1.2.3", "sha256:" + "a" * 64, None

    monkeypatch.setattr(resolver, "_pull_chart_once", _pull_once)
    monkeypatch.setattr(resolver.random, "uniform", lambda _start, _end: 0.0)
    monkeypatch.setattr(resolver.time, "sleep", delays.append)

    result = resolver._pull_chart(
        helm="helm",
        chart="cert-manager",
        version="1.2.3",
        repository="https://charts.example.invalid",
        destination=tmp_path,
    )

    assert result == ("1.2.3", "sha256:" + "a" * 64, None)
    assert len(attempts) == 2
    assert len(set(attempts)) == 2
    assert all(not attempt.exists() for attempt in attempts)
    assert delays == [2.0]


def test_chart_pull_exhausts_three_transient_attempts_with_sanitized_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[Path] = []
    delays: list[float] = []

    def _pull_once(**kwargs):
        attempts.append(kwargs["destination"])
        raise RuntimeError("download failed: read tcp 192.0.2.10:1234: connection reset by peer")

    monkeypatch.setattr(resolver, "_pull_chart_once", _pull_once)
    monkeypatch.setattr(resolver.random, "uniform", lambda _start, _end: 0.0)
    monkeypatch.setattr(resolver.time, "sleep", delays.append)

    with pytest.raises(RuntimeError) as failure:
        resolver._pull_chart(
            helm="helm",
            chart="cert-manager",
            version="1.2.3",
            repository="https://charts.example.invalid",
            destination=tmp_path,
        )

    assert str(failure.value) == (
        "download official chart cert-manager 1.2.3 failed after 3 attempts: connection reset"
    )
    assert "192.0.2.10" not in str(failure.value)
    assert len(attempts) == 3
    assert all(not attempt.exists() for attempt in attempts)
    assert delays == [2.0, 4.0]


def test_chart_pull_exhaustion_does_not_expose_transport_details_in_cli_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines: list[str] = []

    class RecordingConsole:
        def print(self, *values: object, **_kwargs: object) -> None:
            lines.append(" ".join(str(value) for value in values))

    def _fail_pull(**_kwargs: object) -> None:
        raise RuntimeError("read tcp 192.0.2.10:1234: connection reset by peer")

    monkeypatch.setattr(
        resolver,
        "_pull_chart_once",
        _fail_pull,
    )
    monkeypatch.setattr(resolver.random, "uniform", lambda _start, _end: 0.0)
    monkeypatch.setattr(resolver.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(cli, "console", RecordingConsole())

    with pytest.raises(RuntimeError) as failure:
        resolver._pull_chart(
            helm="helm",
            chart="cert-manager",
            version="1.2.3",
            repository="https://charts.example.invalid",
            destination=tmp_path,
        )
    with pytest.raises(typer.Exit):
        cli._exit_with_error(failure.value)

    rendered = "\n".join(lines)
    assert "failed after 3 attempts: connection reset" in rendered
    assert "192.0.2.10" not in rendered
    assert "caused by" not in rendered


@pytest.mark.parametrize(
    "failure",
    (
        RuntimeError("x509: certificate signed by unknown authority"),
        RuntimeError("unauthorized: authentication required"),
        RuntimeError("chart not found"),
        ValueError("downloaded chart identity differs from requested chart"),
    ),
)
def test_chart_pull_never_retries_permanent_or_validation_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    calls = 0

    def _pull_once(**_kwargs):
        nonlocal calls
        calls += 1
        raise failure

    monkeypatch.setattr(resolver, "_pull_chart_once", _pull_once)
    monkeypatch.setattr(
        resolver.time,
        "sleep",
        lambda _seconds: pytest.fail("permanent failures must not back off or retry"),
    )

    with pytest.raises(type(failure), match=str(failure)):
        resolver._pull_chart(
            helm="helm",
            chart="cert-manager",
            version="1.2.3",
            repository="https://charts.example.invalid",
            destination=tmp_path,
        )

    assert calls == 1


def test_chart_metadata_rejects_package_over_compressed_size_limit(tmp_path: Path) -> None:
    package = tmp_path / "chart.tgz"
    _write_chart_package(package, {"chart/Chart.yaml": b"name: chart\nversion: 1.0.0\n"})

    with pytest.raises(ValueError, match="size limit"):
        resolver._chart_metadata_from_package(
            package,
            limits=_chart_limits(max_compressed_bytes=1),
        )


def test_chart_metadata_rejects_package_over_member_count_limit(tmp_path: Path) -> None:
    package = tmp_path / "chart.tgz"
    _write_chart_package(
        package,
        {
            "chart/Chart.yaml": b"name: chart\nversion: 1.0.0\n",
            "chart/values.yaml": b"{}\n",
        },
    )

    with pytest.raises(ValueError, match="file-count limit"):
        resolver._chart_metadata_from_package(
            package,
            limits=_chart_limits(max_members=1),
        )


def test_chart_metadata_rejects_oversized_chart_yaml(tmp_path: Path) -> None:
    package = tmp_path / "chart.tgz"
    _write_chart_package(
        package,
        {"chart/Chart.yaml": b"name: chart\nversion: 1.0.0\n"},
    )

    with pytest.raises(ValueError, match="Chart.yaml exceeds"):
        resolver._chart_metadata_from_package(
            package,
            limits=_chart_limits(max_metadata_bytes=16),
        )


def test_chart_metadata_counts_pax_headers_in_tar_stream_limit(tmp_path: Path) -> None:
    package = tmp_path / "chart.tgz"
    with tarfile.open(package, mode="w:gz") as bundle:
        member = tarfile.TarInfo("chart/Chart.yaml")
        payload = b"name: chart\nversion: 1.0.0\n"
        member.size = len(payload)
        member.pax_headers = {"comment": "x" * 4096}
        bundle.addfile(member, io.BytesIO(payload))

    with pytest.raises(ValueError, match="decompressed tar-stream limit"):
        resolver._chart_metadata_from_package(
            package,
            limits=_chart_limits(max_tar_bytes=1024),
        )


@pytest.mark.parametrize(
    ("files", "limit_overrides"),
    (
        (
            {
                "chart/Chart.yaml": b"name: chart\nversion: 1.0.0\n",
                "chart/values.yaml": b"x" * 64,
            },
            {"max_member_bytes": 32},
        ),
        (
            {
                "chart/Chart.yaml": b"name: chart\nversion: 1.0.0\n",
                "chart/values.yaml": b"x" * 16,
            },
            {"max_expanded_bytes": 32},
        ),
    ),
    ids=("member-size", "expanded-size"),
)
def test_chart_metadata_rejects_expanded_size_limits(
    tmp_path: Path,
    files: dict[str, bytes],
    limit_overrides: dict[str, int],
) -> None:
    package = tmp_path / "chart.tgz"
    _write_chart_package(package, files)

    with pytest.raises(ValueError, match="expanded-size limit"):
        resolver._chart_metadata_from_package(
            package,
            limits=_chart_limits(**limit_overrides),
        )


def test_chart_metadata_rejects_multiply_linked_package(tmp_path: Path) -> None:
    package = tmp_path / "chart.tgz"
    alias = tmp_path / "chart-alias.tgz"
    _write_chart_package(package, {"chart/Chart.yaml": b"name: chart\nversion: 1.0.0\n"})
    os.link(package, alias)

    with pytest.raises(ValueError, match="regular single-link file"):
        resolver._chart_metadata_from_package(package)


def _source(tmp_path: Path, images: dict[str, str]) -> Path:
    root = tmp_path / "source"
    values = root / "helm" / "slurm-cluster" / "values.yaml"
    values.parent.mkdir(parents=True)
    values.write_text(
        yaml.safe_dump({"cudaVersion": "12.9.0", "images": images}),
        encoding="utf-8",
    )
    return root


def test_populate_jail_tag_is_resolved_to_platform_digest(tmp_path: Path, monkeypatch) -> None:
    source = _source(
        tmp_path,
        {
            "populateJail": "",
            "populateJailRepository": "registry.example.invalid/soperator/populate-jail",
            "populateJailTag": "4.1.7",
        },
    )
    seen: list[str] = []

    def resolve(image: str):
        seen.append(image)
        return SimpleNamespace(
            immutable_reference="registry.example.invalid/soperator/populate-jail@sha256:"
            + "a" * 64
        )

    monkeypatch.setattr(resolver, "resolve_oci_image", resolve)

    image, cuda = resolver._populate_jail_identity(source)

    assert seen == ["registry.example.invalid/soperator/populate-jail:4.1.7-cuda12.9.0"]
    assert image.endswith("@sha256:" + "a" * 64)
    assert cuda == "12.9.0"


def test_populate_jail_digest_is_not_reresolved(tmp_path: Path, monkeypatch) -> None:
    image = "registry.example.invalid/soperator/populate-jail@sha256:" + "b" * 64
    source = _source(tmp_path, {"populateJail": image})
    monkeypatch.setattr(
        resolver,
        "resolve_oci_image",
        lambda _image: (_ for _ in ()).throw(AssertionError("must not resolve immutable image")),
    )

    observed, _cuda = resolver._populate_jail_identity(source)

    assert observed == image
