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


def test_release_command_failure_is_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver.subprocess,
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

    monkeypatch.setattr(resolver.subprocess, "run", _timeout)

    with pytest.raises(RuntimeError, match="timed out after 300 seconds") as excinfo:
        resolver._run(["helm", "pull"], label="download official chart")

    assert "release-timeout-sensitive-value" not in str(excinfo.value)
    assert "<redacted>" in str(excinfo.value)


def test_recent_release_snapshot_cache_is_selector_bound_and_expires(tmp_path: Path) -> None:
    snapshot = _snapshot_with_current_mount_image()
    cache_root = tmp_path / "cache"
    path = resolver._write_recent_release_snapshot(
        snapshot,
        selector="4.1.7",
        cache_root=cache_root,
    )
    written_at = path.stat().st_mtime

    cached = resolver._load_recent_release_snapshot(
        "4.1.7",
        cache_root=cache_root,
        now=written_at + 899,
    )

    assert cached is not None
    assert cached.selector == "4.1.7"
    assert cached.release == "4.1.7"
    assert (
        resolver._load_recent_release_snapshot(
            "4.1.7",
            cache_root=cache_root,
            now=written_at + 901,
        )
        is None
    )


def test_freeze_reuses_recent_verified_snapshot_without_github(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_root = tmp_path / "cache"
    resolver._write_recent_release_snapshot(
        _snapshot_with_current_mount_image(),
        selector="4.1.7",
        cache_root=cache_root,
    )
    frozen = SimpleNamespace(metadata=object())
    ledger_calls: list[object] = []

    class _Ledger:
        def __init__(self, _root) -> None:
            pass

        def locked(self, metadata):
            ledger_calls.append(metadata)
            return nullcontext()

    monkeypatch.setattr(resolver, "SoperatorReleaseIdentityLedger", _Ledger)
    monkeypatch.setattr(
        resolver,
        "frozen_soperator_release_from_snapshot",
        lambda _snapshot, **_kwargs: frozen,
    )
    monkeypatch.setattr(
        resolver,
        "resolve_soperator_release",
        lambda *_args, **_kwargs: pytest.fail("recent sealed snapshot must avoid GitHub"),
    )
    progress: list[str] = []

    assert (
        resolver.freeze_soperator_release(
            "4.1.7",
            current_release="1.22.3",
            cache_root=cache_root,
            emit=progress.append,
        )
        is frozen
    )
    assert ledger_calls == [frozen.metadata]
    assert progress == [
        "Cached release snapshot found; re-verifying source and identity",
        "Cached release source and identity re-verified",
    ]


def test_recent_release_snapshot_rejects_stale_adapter_mount_image(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    stale = replace(
        sample_snapshot(),
        mount_image="registry.example.invalid/mount@sha256:" + "0" * 64,
        snapshot_sha256="",
    )
    path = resolver._write_recent_release_snapshot(
        stale,
        selector="4.1.7",
        cache_root=cache_root,
    )

    assert (
        resolver._load_recent_release_snapshot(
            "4.1.7",
            cache_root=cache_root,
            now=path.stat().st_mtime + 1,
        )
        is None
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
