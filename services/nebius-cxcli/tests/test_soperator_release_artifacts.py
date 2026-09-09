from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import nebius_cxcli.soperator_release_artifacts as release_artifacts
from nebius_cxcli.soperator_flux_graph import expected_soperator_release_names
from nebius_cxcli.soperator_release_artifacts import (
    _cache_chart_package,
    _run,
    _verify_rendered_release_graph,
)


def test_child_validation_checks_third_party_schema_and_hides_diagnostics(tmp_path):
    import json
    import shutil

    if not shutil.which("helm"):
        pytest.skip("helm is required to verify child schemas")
    chart = tmp_path / "chart"
    chart.mkdir()
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: example\nversion: 1.0.0\n")
    (chart / "values.yaml").write_text("replicas: 1\n")
    (chart / "values.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"replicas": {"type": "integer"}},
            }
        )
    )
    lock = SimpleNamespace(
        release_graph=(
            SimpleNamespace(
                release_name="dependency",
                chart_key="certManager",
                owner="third-party",
            ),
        )
    )
    consumers = (
        {
            "metadata": {"name": "dependency"},
            "spec": {
                "releaseName": "example",
                "targetNamespace": "consumer",
                "values": {"replicas": "SENSITIVE"},
            },
        },
    )
    with pytest.raises(ValueError, match="certManager chart rejected") as error:
        release_artifacts._validate_child_renders(
            lock,
            consumers,
            {"certManager": chart},
            helm="helm",
            directory=tmp_path,
        )
    assert "SENSITIVE" not in str(error.value)
    consumers[0]["spec"]["values"] = {"replicas": 2}
    release_artifacts._validate_child_renders(
        lock,
        consumers,
        {"certManager": chart},
        helm="helm",
        directory=tmp_path,
    )


def _render(names: set[str]) -> bytes:
    return yaml.safe_dump_all(
        [
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": name, "namespace": "flux-system"},
            }
            for name in sorted(names)
        ]
        + [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "ignored"}}],
        sort_keys=False,
    ).encode()


def test_verified_upstream_render_must_equal_enabled_release_graph() -> None:
    values: dict[str, object] = {
        "observability": {"enabled": False},
        "nodesets": {"enabled": False},
        "storageClasses": {"enabled": False},
        "backup": {"enabled": False},
    }

    _verify_rendered_release_graph(
        _render(set(expected_soperator_release_names(values))),
        values,
    )


def test_artifact_chart_reader_counts_pax_headers_in_tar_stream_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "chart.tgz"
    with tarfile.open(package, mode="w:gz") as bundle:
        for name, payload in (
            ("chart/Chart.yaml", b"name: chart\nversion: 1.0.0\n"),
            ("chart/values.yaml", b"{}\n"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.pax_headers = {"comment": "x" * 4096}
            bundle.addfile(member, io.BytesIO(payload))
    monkeypatch.setattr(release_artifacts, "_MAX_CHART_TAR_BYTES", 1024)

    with pytest.raises(ValueError, match="decompressed tar-stream limit"):
        release_artifacts._chart_file_map(package)


def test_artifact_command_failure_redacts_and_bounds_subprocess_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "do-not-expose-this-token"
    private_url = f"https://user:{secret}@private.example.invalid/chart"
    raw_detail = f'pull failed for {private_url}\n{{"access_token":"{secret}"}}\n' + ("x" * 4096)
    monkeypatch.setattr(
        release_artifacts.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            stdout="",
            stderr=raw_detail,
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        _run(["helm", "pull"], label="download chart")

    message = str(exc_info.value)
    assert secret not in message
    assert "private.example.invalid" not in message
    assert "<url>" in message
    assert "<redacted>" in message
    assert len(message) < 2300


def test_artifact_command_timeout_redacts_captured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "do-not-expose-timeout-token"

    def _time_out(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise subprocess.TimeoutExpired(
            cmd=("helm", "pull"),
            timeout=300,
            stderr=f'{{"password":"{secret}"}}'.encode(),
        )

    monkeypatch.setattr(release_artifacts.subprocess, "run", _time_out)

    with pytest.raises(RuntimeError, match="timed out after 300 seconds") as exc_info:
        _run(["helm", "pull"], label="download chart")

    message = str(exc_info.value)
    assert secret not in message
    assert "<redacted>" in message


@pytest.mark.parametrize("drift", ["missing", "unexpected"])
def test_verified_upstream_render_rejects_graph_drift(drift: str) -> None:
    values: dict[str, object] = {"observability": {"enabled": False}}
    names = set(expected_soperator_release_names(values))
    if drift == "missing":
        names.remove("soperator-fluxcd-slurm-cluster")
    else:
        names.add("soperator-fluxcd-unknown")

    with pytest.raises(ValueError, match=drift):
        _verify_rendered_release_graph(_render(names), values)


def test_corrupt_chart_cache_is_refetched_from_exact_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_bytes = b"exact official chart bytes"
    expected = f"sha256:{hashlib.sha256(package_bytes).hexdigest()}"
    calls = 0

    def _fake_run(command: list[str], *, label: str) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del label
        calls += 1
        destination = Path(command[command.index("--destination") + 1])
        (destination / "raw-2.0.0.tgz").write_bytes(package_bytes)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(release_artifacts, "_run", _fake_run)
    cache = tmp_path / "cache"
    first = _cache_chart_package(
        helm="helm",
        chart="raw",
        version="2.0.0",
        repository="https://bedag.github.io/helm-charts",
        expected_sha256=expected,
        expected_oci_digest=None,
        cache_dir=cache,
    )
    first.chmod(0o600)
    first.write_bytes(b"corrupt")
    repaired = _cache_chart_package(
        helm="helm",
        chart="raw",
        version="2.0.0",
        repository="https://bedag.github.io/helm-charts",
        expected_sha256=expected,
        expected_oci_digest=None,
        cache_dir=cache,
    )

    assert calls == 2
    assert repaired.read_bytes() == package_bytes


@pytest.mark.parametrize("drift", [None, "package", "manifest"])
def test_cold_oci_cache_uses_frozen_manifest_when_version_tag_moves(tmp_path, monkeypatch, drift):
    original = b"approved chart"
    sha = "sha256:" + hashlib.sha256(original).hexdigest()
    manifest = "sha256:" + "a" * 64

    def pull(command, *, label):
        assert command[2] == "oci://registry.example.invalid/charts/checks@" + manifest
        assert "--version" not in command
        destination = Path(command[command.index("--destination") + 1])
        (destination / "checks-1.0.0.tgz").write_bytes(
            b"changed tag" if drift == "package" else original
        )
        return subprocess.CompletedProcess(
            command, 0, "", "Digest: " + ("sha256:wrong" if drift == "manifest" else manifest)
        )

    monkeypatch.setattr(release_artifacts, "_run", pull)
    kwargs = dict(
        helm="helm",
        chart="checks",
        version="1.0.0",
        repository="oci://registry.example.invalid/charts",
        expected_sha256=sha,
        expected_oci_digest=manifest,
        cache_dir=tmp_path / "cache",
    )
    if drift:
        with pytest.raises(ValueError, match="digest mismatch"):
            _cache_chart_package(**kwargs)
        assert not list((tmp_path / "cache").glob("*.tgz"))
    else:
        assert _cache_chart_package(**kwargs).read_bytes() == original
