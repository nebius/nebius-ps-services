from __future__ import annotations

import copy
import io
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli.compatibility_artifacts import (
    bind_flux_artifacts,
    capture_chart,
    frozen_chart_inputs,
    reference_key,
    resolve_chart_input,
)
from nebius_cxcli.compatibility_matrix import digest
from nebius_cxcli.helm_client import HelmChartReference, HelmClient, _materialize_chart_dir


@pytest.fixture
def oci_chart_download(monkeypatch):
    import nebius_cxcli.soperator_release_resolver as resolver

    reference = HelmChartReference(
        "sample", "oci://registry.example.invalid/charts/sample", "1.0.0"
    )
    attempts = []
    delays = []
    files = {
        "sample/Chart.yaml": b"apiVersion: v2\nname: sample\nversion: 1.0.0\n",
        "sample/values.yaml": b"enabled: true\n",
    }

    def run(outcome=None, *, digest=True):
        def download(command, **_kwargs):
            directory = Path(command[command.index("--destination") + 1])
            assert command[2] == reference.chart_repo
            assert command[command.index("--version") + 1] == reference.chart_version
            assert all(not path.exists() for path in attempts)
            attempts.append(directory)
            # A failed transfer must not contaminate the next attempt.
            (directory / "partial.tgz").write_bytes(b"partial")
            if outcome is not None:
                result = outcome(len(attempts))
                if result is not None:
                    return result
            (directory / "partial.tgz").unlink()
            with tarfile.open(directory / "sample-1.0.0.tgz", "w:gz") as archive:
                for name, data in files.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            return subprocess.CompletedProcess(
                command, 0, "", "Digest: sha256:" + "a" * 64 if digest else ""
            )

        monkeypatch.setattr(resolver.kubernetes_process, "run", download)

    monkeypatch.setattr(resolver.time, "sleep", delays.append)
    monkeypatch.setattr(resolver.random, "uniform", lambda *_args: 0.0)
    return SimpleNamespace(
        reference=reference, attempts=attempts, delays=delays, files=files, run=run
    )


@pytest.mark.parametrize("failure", ["connection reset by peer", "i/o timeout", "process-timeout"])
def test_oci_chart_capture_retries_transient_fetch_with_clean_package(oci_chart_download, failure):
    fixture = oci_chart_download

    def outcome(attempt):
        if attempt != 1:
            return None
        if failure == "process-timeout":
            raise subprocess.TimeoutExpired(["helm", "pull"], timeout=300)
        return subprocess.CompletedProcess([], 1, "", failure)

    fixture.run(outcome)
    captured = resolve_chart_input(fixture.reference)
    assert len(fixture.attempts) == 2
    assert len(set(fixture.attempts)) == 2
    assert fixture.delays == [2.0]
    assert all(not path.exists() for path in fixture.attempts)
    assert captured["oci_digest"] == "sha256:" + "a" * 64
    with frozen_chart_inputs({reference_key(fixture.reference): captured}):
        assert resolve_chart_input(fixture.reference) == captured
        assert HelmClient().show_values(reference=fixture.reference) == {"enabled": True}
    assert len(fixture.attempts) == 2


def test_oci_chart_capture_exhausts_transient_fetch_without_partial_input(oci_chart_download):
    fixture = oci_chart_download
    fixture.run(
        lambda _attempt: subprocess.CompletedProcess(
            [],
            1,
            "",
            "Get https://registry.example.invalid/blob?signature=sentinel: connection reset by peer",
        )
    )
    with pytest.raises(RuntimeError) as error:
        resolve_chart_input(fixture.reference)
    assert str(error.value) == (
        "download official chart sample 1.0.0 failed after 3 attempts: connection reset"
    )
    assert len(fixture.attempts) == 3
    assert fixture.delays == [2.0, 4.0]
    assert all(not path.exists() for path in fixture.attempts)


@pytest.mark.parametrize("detail", ["unauthorized", "x509 certificate failure", "chart not found"])
def test_oci_chart_capture_does_not_retry_permanent_errors(oci_chart_download, detail):
    fixture = oci_chart_download
    fixture.run(lambda _attempt: subprocess.CompletedProcess([], 1, "", detail))
    with pytest.raises(RuntimeError, match=detail):
        resolve_chart_input(fixture.reference)
    assert len(fixture.attempts) == 1
    assert fixture.delays == []
    assert all(not path.exists() for path in fixture.attempts)


@pytest.mark.parametrize("failure", ["digest", "identity", "archive"])
def test_oci_chart_capture_does_not_retry_invalid_artifacts(oci_chart_download, failure):
    fixture = oci_chart_download
    if failure == "identity":
        fixture.files["sample/Chart.yaml"] = b"name: unexpected\nversion: 1.0.0\n"
    elif failure == "archive":
        fixture.files["sample/Chart.yaml"] = b"not: [valid"
    fixture.run(digest=failure != "digest")
    with pytest.raises((ValueError, yaml.YAMLError)):
        resolve_chart_input(fixture.reference)
    assert len(fixture.attempts) == 1
    assert fixture.delays == []
    assert all(not path.exists() for path in fixture.attempts)


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_oci_chart_capture_cleans_package_without_retrying_consumer_failure(
    oci_chart_download, monkeypatch, error_type
):
    import nebius_cxcli.soperator_release_artifacts as artifacts

    fixture = oci_chart_download
    fixture.run()

    def reject(package):
        assert package.is_file()
        raise error_type("consumer connection reset")

    monkeypatch.setattr(artifacts, "_chart_file_map", reject)
    with pytest.raises(error_type, match="consumer connection reset"):
        resolve_chart_input(fixture.reference)
    assert len(fixture.attempts) == 1
    assert fixture.delays == []
    assert all(not path.exists() for path in fixture.attempts)


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, FileNotFoundError])
def test_oci_chart_capture_cleans_interrupted_or_failed_launch(oci_chart_download, error_type):
    fixture = oci_chart_download

    def outcome(_attempt):
        raise error_type()

    fixture.run(outcome)
    with pytest.raises(error_type):
        resolve_chart_input(fixture.reference)
    assert len(fixture.attempts) == 1
    assert fixture.delays == []
    assert all(not path.exists() for path in fixture.attempts)


def test_frozen_chart_replay_ignores_changed_or_missing_source(tmp_path):
    reference = HelmChartReference(str(tmp_path), "", "1.0.0")
    (tmp_path / "Chart.yaml").write_text(
        "apiVersion: v2\nname: sample\nversion: 1.0.0\nkubeVersion: '>=1.31.0'\n"
    )
    (tmp_path / "values.yaml").write_text("enabled: true\n")
    captured = capture_chart(reference, tmp_path)
    (tmp_path / "Chart.yaml").unlink()
    (tmp_path / "values.yaml").write_text("enabled: false\n")
    with frozen_chart_inputs({reference_key(reference): captured}):
        assert HelmClient().show_chart(reference=reference)["version"] == "1.0.0"
        assert HelmClient().show_values(reference=reference) == {"enabled": True}
        with _materialize_chart_dir(reference) as directory:
            assert (directory / "Chart.yaml").is_file()
        with pytest.raises(ValueError, match="absent from the frozen"):
            HelmClient().show_chart(reference=HelmChartReference("other", "", "1.0.0"))


def test_frozen_chart_rejects_path_escape_before_extract(tmp_path):
    reference = HelmChartReference(str(tmp_path), "", "1.0.0")
    (tmp_path / "Chart.yaml").write_text("name: sample\n")
    captured = capture_chart(reference, tmp_path)
    altered = copy.deepcopy(captured)
    altered["files"]["../escape"] = "eA=="
    altered["sha256"] = digest({k: v for k, v in altered.items() if k != "sha256"})
    with (
        pytest.raises(ValueError, match="Unsafe"),
        frozen_chart_inputs({reference_key(reference): altered}),
    ):
        pytest.fail("unsafe input admitted")


def test_oci_execution_uses_assessed_digest_for_every_release(tmp_path):
    reference = HelmChartReference("sample", "oci://registry.test/charts/sample", "1.0.0")
    source = tmp_path / "source"
    source.mkdir()
    (source / "Chart.yaml").write_text("name: sample\nversion: 1.0.0\n")
    captured = capture_chart(reference, source, oci_digest="sha256:" + "a" * 64)
    flux = tmp_path / "flux"
    flux.mkdir()
    repository = {
        "kind": "HelmRepository",
        "metadata": {
            "name": "sample",
            "namespace": "flux-system",
            "labels": {"component": "ordinary"},
            "annotations": {"cxcli.nebius.com/app-owner": "accepted-project"},
        },
        "spec": {"type": "oci", "url": "oci://registry.test/charts"},
    }
    (flux / "repositories.yaml").write_text(yaml.safe_dump(repository))
    for name in ("one", "two"):
        release = {
            "kind": "HelmRelease",
            "metadata": {"name": name},
            "spec": {
                "chart": {
                    "spec": {
                        "chart": "sample",
                        "version": "1.0.0",
                        "sourceRef": {
                            "kind": "HelmRepository",
                            "name": "sample",
                            "namespace": "flux-system",
                        },
                    }
                }
            },
        }
        (flux / (name + ".yaml")).write_text(yaml.safe_dump(release))
    bind_flux_artifacts(SimpleNamespace(flux_dir=flux), {reference_key(reference): captured})
    source_doc = yaml.safe_load((flux / "repositories.yaml").read_text())
    assert source_doc["kind"] == "OCIRepository"
    assert source_doc["metadata"] == repository["metadata"]
    assert source_doc["spec"]["url"] == reference.chart_repo
    assert source_doc["spec"]["ref"] == {"digest": captured["oci_digest"]}
    for name in ("one", "two"):
        release = yaml.safe_load((flux / (name + ".yaml")).read_text())
        assert "chart" not in release["spec"]
        assert release["spec"]["chartRef"]["kind"] == "OCIRepository"


def test_native_enabled_dependency_constraints_respect_conditions(tmp_path):
    from nebius_cxcli.compatibility_adapters import enabled_chart_constraints

    (tmp_path / "Chart.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v2",
                "name": "parent",
                "version": "1.0.0",
                "dependencies": [
                    {"name": "child", "version": "1.0.0", "condition": "child.enabled"}
                ],
            }
        )
    )
    child = tmp_path / "charts/child"
    child.mkdir(parents=True)
    (child / "Chart.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v2",
                "name": "child",
                "version": "1.0.0",
                "kubeVersion": "<1.35.0",
            }
        )
    )
    values = tmp_path / "test-values.yaml"
    values.write_text("child:\n  enabled: false\n")
    assert enabled_chart_constraints(tmp_path, values, "1.35.0") == []
    values.write_text("child:\n  enabled: true\n")
    with pytest.raises(ValueError, match="Helm rejected"):
        enabled_chart_constraints(tmp_path, values, "1.35.0")
    proof = enabled_chart_constraints(tmp_path, values, "1.34.0")
    assert proof[0]["metadata"]["name"] == "child"
    assert proof[0]["constraint"]["outcome"] == "pass"
    assert not list(tmp_path.glob("templates/cxcli_compatibility_*"))


@pytest.mark.parametrize("constraint_metadata", [{}, {"kubeVersion": ""}])
def test_native_enabled_dependency_without_constraint(tmp_path, constraint_metadata):
    from nebius_cxcli.compatibility_adapters import enabled_chart_constraints

    (tmp_path / "Chart.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v2",
                "name": "parent",
                "version": "1.0.0",
                "dependencies": [
                    {"name": "child", "version": "1.0.0", "condition": "child.enabled"}
                ],
            }
        )
    )
    child = tmp_path / "charts/child"
    child.mkdir(parents=True)
    metadata_path = child / "Chart.yaml"
    metadata_path.write_text(
        yaml.safe_dump(
            {"apiVersion": "v2", "name": "child", "version": "1.0.0", **constraint_metadata}
        )
    )
    original_metadata = metadata_path.read_bytes()
    values = tmp_path / "test-values.yaml"
    values.write_text("child:\n  enabled: false\n")
    assert enabled_chart_constraints(tmp_path, values, "1.36") == []
    values.write_text("child:\n  enabled: true\n")
    try:
        proof = enabled_chart_constraints(tmp_path, values, "1.36")
        assert proof == [
            {
                "metadata": {"name": "child", "version": "1.0.0", "kubeVersion": ""},
                "constraint": {"outcome": "not_declared", "constraint": None},
            }
        ]
    finally:
        assert metadata_path.read_bytes() == original_metadata
        assert not list(tmp_path.glob("templates/cxcli_compatibility_*"))
