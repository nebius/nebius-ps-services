from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_cxcli.helm_client import (
    HelmChartReference,
    HelmClient,
    _materialize_chart_dir,
    _resolve_show_ref,
    _run_git_clone,
    _run_helm_show,
    chart_cli_contract_findings,
    render_chart_template_documents,
)


@contextmanager
def _yield_path(path: Path):
    yield path


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


def test_resolve_show_ref_http_repo_without_index_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_resolve_show_ref_github_tree_chart_name_supported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout_dir = tmp_path / "checkout" / "repo"
    chart_dir = checkout_dir / "charts" / "nccl-test"
    chart_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "nebius_cxcli.helm_client._run_git_clone",
        lambda _git_url, _git_ref: checkout_dir,
    )

    show_ref, repo, version, cleanup_dir = _resolve_show_ref(
        HelmChartReference(
            chart_name="https://github.com/example/charts/tree/main/charts/nccl-test",
            chart_repo="",
            chart_version="",
        )
    )

    assert show_ref == str(chart_dir.resolve())
    assert repo == ""
    assert version == ""
    assert cleanup_dir == checkout_dir.parent


def test_run_git_clone_requires_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nebius_cxcli.helm_client.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "nebius_cxcli.helm_client.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("git clone should not run when git is missing"),
    )

    with pytest.raises(
        RuntimeError,
        match="git is required for Git tree Helm chart sources but was not found in PATH",
    ):
        _run_git_clone("https://github.com/example/charts.git", "main")


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


def test_search_repo_uses_stable_repo_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nebius_cxcli.helm_client.shutil.which", lambda _cmd: "/usr/bin/helm")
    monkeypatch.setattr("nebius_cxcli.helm_client._repo_has_index", lambda _repo: True)
    commands: list[list[str]] = []

    def _fake_run(args: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(args)
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr("nebius_cxcli.helm_client.subprocess.run", _fake_run)

    repo = "https://charts.example.test/team"
    result = HelmClient().search_repo(chart_name="soperator", chart_repo=f"{repo}/")

    expected_alias = f"cxcli-{hashlib.sha1(repo.encode('utf-8')).hexdigest()[:12]}"
    assert result == []
    assert commands[0] == ["helm", "repo", "add", expected_alias, repo]
    assert commands[1][:4] == ["helm", "search", "repo", f"{expected_alias}/soperator"]


def test_run_helm_show_uses_configured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, int] = {}

    def _fake_run(*_args, **kwargs):  # type: ignore[no-untyped-def]
        observed["timeout"] = kwargs["timeout"]
        return SimpleNamespace(
            returncode=0,
            stdout="apiVersion: v2\nname: demo\nversion: 1.0.0\n",
            stderr="",
        )

    monkeypatch.setenv("NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS", "321")
    monkeypatch.setattr("nebius_cxcli.helm_client.subprocess.run", _fake_run)

    output = _run_helm_show("chart", "oci://docker.io/example/demo", version="1.0.0")

    assert "apiVersion: v2" in output
    assert observed["timeout"] == 321


def test_materialize_chart_dir_uses_local_path_without_pull(tmp_path: Path) -> None:
    chart_dir = tmp_path / "demo-chart"
    chart_dir.mkdir()
    (chart_dir / "Chart.yaml").write_text(
        "apiVersion: v2\nname: demo\nversion: 1.0.0\n", encoding="utf-8"
    )

    with _materialize_chart_dir(
        HelmChartReference(chart_name=str(chart_dir), chart_repo="", chart_version="")
    ) as materialized:
        assert materialized == chart_dir


def test_chart_cli_contract_findings_accepts_minimal_chart_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chart_dir = tmp_path / "gateway-helm"
    templates_dir = chart_dir / "templates"
    templates_dir.mkdir(parents=True)
    (chart_dir / "Chart.yaml").write_text(
        "apiVersion: v2\nname: gateway-helm\nversion: 1.4.2\n",
        encoding="utf-8",
    )
    (chart_dir / "values.yaml").write_text("service: {}\n", encoding="utf-8")
    (templates_dir / "deployment.yaml").write_text("kind: Deployment\n", encoding="utf-8")
    (chart_dir / "README.md").write_text("# demo\n", encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.helm_client._materialize_chart_dir",
        lambda _reference: _yield_path(chart_dir),
    )
    chart_cli_contract_findings.cache_clear()

    issues, warnings = chart_cli_contract_findings(
        chart_name="gateway-helm",
        chart_repo="oci://docker.io/envoyproxy/gateway-helm",
        chart_version="1.4.2",
    )

    assert issues == ()
    assert warnings == ()


def test_chart_cli_contract_findings_reports_missing_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chart_dir = tmp_path / "broken-chart"
    chart_dir.mkdir(parents=True)
    (chart_dir / "values.yaml").write_text("service: {}\n", encoding="utf-8")

    monkeypatch.setattr(
        "nebius_cxcli.helm_client._materialize_chart_dir",
        lambda _reference: _yield_path(chart_dir),
    )
    chart_cli_contract_findings.cache_clear()

    issues, warnings = chart_cli_contract_findings(
        chart_name="gateway-helm",
        chart_repo="oci://docker.io/envoyproxy/gateway-helm",
        chart_version="1.4.2",
    )

    assert any("missing Chart.yaml" in issue for issue in issues)
    assert any("missing templates/" in issue for issue in issues)
    assert not any("missing README.md" in warning for warning in warnings)

    chart_cli_contract_findings.cache_clear()
    _issues, warnings = chart_cli_contract_findings(
        chart_name=str(chart_dir),
        chart_repo="",
        chart_version="",
    )

    assert any("missing README.md" in warning for warning in warnings)


def test_render_chart_template_documents_parses_rendered_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chart_dir = tmp_path / "demo-chart"
    chart_dir.mkdir(parents=True)

    @contextmanager
    def _materialized(_reference: HelmChartReference):
        yield chart_dir

    def _fake_run(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            returncode=0,
            stdout="apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: demo\n",
            stderr="",
        )

    monkeypatch.setattr("nebius_cxcli.helm_client._materialize_chart_dir", _materialized)
    monkeypatch.setattr("nebius_cxcli.helm_client.subprocess.run", _fake_run)

    documents = render_chart_template_documents(
        chart_name=str(chart_dir),
        chart_repo="",
        chart_version="",
        release_name="demo",
        namespace="default",
        values={"foo": "bar"},
    )

    assert documents == [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "demo"}}]
