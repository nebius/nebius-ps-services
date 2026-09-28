from __future__ import annotations

from contextlib import contextmanager

import pytest
import yaml

from nebius_cxcli.compatibility_artifacts import (
    admit_chart_sources,
    capture_chart,
    frozen_chart,
    frozen_chart_inputs,
    reference_key,
)
from nebius_cxcli.helm_client import HelmChartReference


@pytest.fixture
def http_chart(tmp_path, monkeypatch):
    reference = HelmChartReference("sample", "https://charts.example.test", "1.0.0")
    (tmp_path / "Chart.yaml").write_text("apiVersion: v2\nname: sample\nversion: 1.0.0\n")
    (tmp_path / "values.yaml").write_text("enabled: true\n")
    snapshot = capture_chart(reference, tmp_path)
    downloads = []

    @contextmanager
    def download(selected):
        assert frozen_chart(selected) is None
        downloads.append(selected)
        yield tmp_path

    monkeypatch.setattr("nebius_cxcli.helm_client._materialize_chart_dir", download)
    return reference, snapshot, tmp_path, downloads


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_http_admission_refreshes_inside_nested_frozen_contexts(http_chart, scheme):
    reference, snapshot, root, downloads = http_chart
    reference = HelmChartReference("sample", f"{scheme}://charts.example.test", "1.0.0")
    snapshot = capture_chart(reference, root)
    inputs = {reference_key(reference): snapshot}
    with frozen_chart_inputs(inputs), frozen_chart_inputs(inputs):
        admit_chart_sources(inputs)
        assert frozen_chart(reference) == snapshot
    assert downloads == [reference]


def test_http_admission_rejects_republished_content_and_restores_context(http_chart):
    reference, snapshot, root, downloads = http_chart
    inputs = {reference_key(reference): snapshot}
    (root / "values.yaml").write_text("enabled: false\n")
    with frozen_chart_inputs(inputs):
        with pytest.raises(ValueError, match="contents changed since render"):
            admit_chart_sources(inputs)
        assert frozen_chart(reference) == snapshot
    assert downloads == [reference]


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_http_admission_restores_context_after_download_failure(http_chart, monkeypatch, failure):
    reference, snapshot, _, _ = http_chart
    inputs = {reference_key(reference): snapshot}

    def fail(_reference):
        raise failure("https://private.invalid?token=do-not-display")

    monkeypatch.setattr("nebius_cxcli.compatibility_artifacts.resolve_chart_input", fail)
    expected = ValueError if failure is RuntimeError else KeyboardInterrupt
    with frozen_chart_inputs(inputs):
        with pytest.raises(expected) as error:
            admit_chart_sources(inputs)
        assert frozen_chart(reference) == snapshot
    if failure is RuntimeError:
        assert "Cannot refresh" in str(error.value)
        assert "token" not in str(error.value)


@pytest.mark.parametrize("version", ["", "latest", "1.*", ">=1.0.0", "1.0"])
def test_http_admission_rejects_nonexact_version_before_download(http_chart, version):
    _, _, root, downloads = http_chart
    reference = HelmChartReference("sample", "https://charts.example.test", version)
    snapshot = capture_chart(reference, root)
    with pytest.raises(ValueError, match="exact.*chart version"):
        admit_chart_sources({reference_key(reference): snapshot})
    assert not downloads


@pytest.mark.parametrize("field,value", [("name", "wrong"), ("version", "1.0.1")])
def test_http_admission_rejects_wrong_download_identity(http_chart, field, value):
    reference, snapshot, root, _ = http_chart
    metadata = yaml.safe_load((root / "Chart.yaml").read_text())
    metadata[field] = value
    (root / "Chart.yaml").write_text(yaml.safe_dump(metadata))
    with pytest.raises(ValueError, match="identity differs"):
        admit_chart_sources({reference_key(reference): snapshot})


@pytest.mark.parametrize(
    "repository", ["https://github.com/org/repo/tree/main/chart", "git://host/chart"]
)
def test_git_chart_stays_blocked_without_download(http_chart, repository):
    _, _, root, downloads = http_chart
    reference = HelmChartReference("sample", repository, "1.0.0")
    snapshot = capture_chart(reference, root)
    with pytest.raises(ValueError, match="HTTP Helm repository or OCI"):
        admit_chart_sources({reference_key(reference): snapshot})
    assert not downloads


@pytest.mark.parametrize(
    "chart_name",
    [
        "https://github.com/org/repo/tree/main/chart",
        "github.com/org/repo/tree/main/chart",
        "oci://registry.test/sample",
    ],
)
def test_remote_chart_name_cannot_bypass_source_admission(http_chart, chart_name):
    _, _, root, downloads = http_chart
    reference = HelmChartReference(chart_name, "", "1.0.0")
    snapshot = capture_chart(reference, root)
    with pytest.raises(ValueError, match="explicit repository"):
        admit_chart_sources({reference_key(reference): snapshot})
    assert not downloads


def test_oci_and_local_admission_do_not_refetch(http_chart):
    _, _, root, downloads = http_chart
    for repository, digest in [("", None), ("oci://registry.test/sample", "sha256:" + "a" * 64)]:
        reference = HelmChartReference("sample", repository, "1.0.0")
        snapshot = capture_chart(reference, root, oci_digest=digest)
        admit_chart_sources({reference_key(reference): snapshot})
    assert not downloads


def test_oci_without_digest_stays_blocked(http_chart):
    _, _, root, downloads = http_chart
    reference = HelmChartReference("sample", "oci://registry.test/sample", "1.0.0")
    snapshot = capture_chart(reference, root)
    with pytest.raises(ValueError, match="OCI chart has no immutable execution binding"):
        admit_chart_sources({reference_key(reference): snapshot})
    assert not downloads
