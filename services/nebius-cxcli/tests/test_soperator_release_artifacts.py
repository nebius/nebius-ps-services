from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

import nebius_cxcli.soperator_release_artifacts as release_artifacts
from nebius_cxcli.soperator_flux_graph import expected_soperator_release_names
from nebius_cxcli.soperator_release_artifacts import (
    _cache_chart_package,
    _run,
    _verify_rendered_release_graph,
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
