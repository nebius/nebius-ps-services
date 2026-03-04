from __future__ import annotations

from pathlib import Path

import pytest

from nebius_cxcli.helm_client import HelmChartReference, HelmClient, _resolve_show_ref


def test_resolve_show_ref_oci_repo_with_chart_name_keeps_single_ref() -> None:
    show_ref, repo, version, cleanup_dir = _resolve_show_ref(
        HelmChartReference(
            chart_name="gateway-helm",
            chart_repo="oci://docker.io/envoyproxy/gateway-helm",
            chart_version="1.4.2",
        )
    )
    assert show_ref == "oci://docker.io/envoyproxy/gateway-helm"
    assert repo == ""
    assert version == "1.4.2"
    assert cleanup_dir is None


def test_resolve_show_ref_oci_repo_prefix_appends_chart_name() -> None:
    show_ref, repo, version, cleanup_dir = _resolve_show_ref(
        HelmChartReference(
            chart_name="gateway-helm",
            chart_repo="oci://docker.io/envoyproxy",
            chart_version="1.4.2",
        )
    )
    assert show_ref == "oci://docker.io/envoyproxy/gateway-helm"
    assert repo == ""
    assert version == "1.4.2"
    assert cleanup_dir is None


def test_resolve_show_ref_http_repo_without_index_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nebius_cxcli.helm_client._repo_has_index", lambda _repo: False)

    with pytest.raises(RuntimeError, match="missing '/index.yaml'"):
        _resolve_show_ref(
            HelmChartReference(
                chart_name="gateway-helm",
                chart_repo="https://envoyproxy.github.io/gateway-helm",
                chart_version="1.4.2",
            )
        )


def test_resolve_show_ref_github_tree_repo_supported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout_dir = tmp_path / "checkout" / "repo"
    chart_dir = checkout_dir / "charts" / "n8n"
    chart_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "nebius_cxcli.helm_client._run_git_clone",
        lambda _git_url, _git_ref: checkout_dir,
    )

    show_ref, repo, version, cleanup_dir = _resolve_show_ref(
        HelmChartReference(
            chart_name="n8n",
            chart_repo="https://github.com/example/charts/tree/main/charts/n8n",
            chart_version="1.0.0",
        )
    )
    assert show_ref == str(chart_dir.resolve())
    assert repo == ""
    assert version == ""
    assert cleanup_dir == checkout_dir.parent


def test_search_repo_skips_oci_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nebius_cxcli.helm_client.shutil.which", lambda _cmd: "/usr/bin/helm")

    invoked = {"value": False}

    def _fail_if_called(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        invoked["value"] = True
        raise AssertionError("subprocess.run should not be called for OCI repo search")

    monkeypatch.setattr("nebius_cxcli.helm_client.subprocess.run", _fail_if_called)

    client = HelmClient()
    result = client.search_repo(chart_name="gateway-helm", chart_repo="oci://docker.io/envoyproxy")
    assert result == []
    assert invoked["value"] is False
