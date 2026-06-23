from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from nebius_cxcli.release_catalog import (
    _build_parser,
    render_release_catalog,
    verify_catalog,
    verify_wheel,
    verify_wheel_bundle,
)


def _catalog_payload(
    portable_source: str,
    *,
    local_source: str | None = "../../platform-infra/modules/mk8s",
    chart_portable_repo: str | None = None,
    chart_local_path: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "components": {
            "infra": {
                "mk8s": {
                    "source": {
                        "portable": portable_source,
                        "local": local_source,
                    }
                }
            }
        }
    }
    if chart_portable_repo is not None or chart_local_path is not None:
        chart_source: dict[str, Any] = {}
        if chart_portable_repo is not None:
            chart_source["portable"] = {
                "repo": chart_portable_repo,
                "chart": "nccl-test",
            }
        if chart_local_path is not None:
            chart_source["local"] = {
                "path": chart_local_path,
            }
        payload["components"]["apps"] = {
            "nccl-test": {
                "source": chart_source,
                "usage": {
                    "lifecycle": "transient",
                },
            }
        }
    return payload


def test_render_release_catalog_rewrites_internal_repo_refs(tmp_path: Path) -> None:
    input_path = tmp_path / "component_sources.yaml"
    output_path = tmp_path / "component_sources.yaml"
    input_path.write_text(
        yaml.safe_dump(
            _catalog_payload(
                "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main"
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    render_release_catalog(
        input_path=input_path,
        output_path=output_path,
        release_ref="nebius-cxcli-v0.1.1",
    )

    payload = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    source = payload["components"]["infra"]["mk8s"]["source"]["portable"]
    assert source.endswith("?ref=nebius-cxcli-v0.1.1")
    assert "local" not in payload["components"]["infra"]["mk8s"]["source"]


def test_render_release_catalog_rewrites_internal_chart_tree_refs(tmp_path: Path) -> None:
    input_path = tmp_path / "component_sources.yaml"
    output_path = tmp_path / "component_sources.yaml"
    input_path.write_text(
        yaml.safe_dump(
            _catalog_payload(
                "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main",
                chart_portable_repo="https://github.com/nebius/nebius-ps-services/tree/main/helm-charts/nccl-test",
                chart_local_path="../../helm-charts/nccl-test",
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    render_release_catalog(
        input_path=input_path,
        output_path=output_path,
        release_ref="nebius-cxcli-v0.1.1",
    )

    payload = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    chart_repo = payload["components"]["apps"]["nccl-test"]["source"]["portable"]["repo"]
    assert chart_repo.endswith("/tree/nebius-cxcli-v0.1.1/helm-charts/nccl-test")
    assert "local" not in payload["components"]["apps"]["nccl-test"]["source"]
    assert payload["components"]["apps"]["nccl-test"]["usage"] == {
        "lifecycle": "transient",
    }


def test_render_release_catalog_keeps_oci_chart_refs_unchanged(tmp_path: Path) -> None:
    input_path = tmp_path / "component_sources.yaml"
    output_path = tmp_path / "component_sources.yaml"
    input_path.write_text(
        yaml.safe_dump(
            _catalog_payload(
                "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main",
                chart_portable_repo="oci://cr.eu-north1.nebius.cloud/e00th0mgv3zddz7468/charts/nccl-test",
                chart_local_path="../../helm-charts/nccl-test",
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    render_release_catalog(
        input_path=input_path,
        output_path=output_path,
        release_ref="nebius-cxcli-v0.1.1",
    )

    payload = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    chart_repo = payload["components"]["apps"]["nccl-test"]["source"]["portable"]["repo"]
    assert chart_repo == "oci://cr.eu-north1.nebius.cloud/e00th0mgv3zddz7468/charts/nccl-test"
    assert "local" not in payload["components"]["apps"]["nccl-test"]["source"]


def test_verify_catalog_rejects_external_main_ref(tmp_path: Path) -> None:
    catalog_path = tmp_path / "component_sources.yaml"
    catalog_path.write_text(
        yaml.safe_dump(
            _catalog_payload("git::https://github.com/example/infra.git//modules/mk8s?ref=main"),
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"incorrectly pinned infra sources"):
        verify_catalog(catalog_path=catalog_path, release_ref="nebius-cxcli-v0.1.1")


def test_verify_catalog_rejects_local_portable_source(tmp_path: Path) -> None:
    catalog_path = tmp_path / "component_sources.yaml"
    catalog_path.write_text(
        yaml.safe_dump(
            _catalog_payload("../../platform-infra/modules/mk8s", local_source=None),
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"incorrectly pinned infra sources"):
        verify_catalog(catalog_path=catalog_path, release_ref="nebius-cxcli-v0.1.1")


def test_verify_catalog_rejects_missing_portable_app_source(tmp_path: Path) -> None:
    catalog_path = tmp_path / "component_sources.yaml"
    catalog_path.write_text(
        yaml.safe_dump(
            _catalog_payload(
                "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=nebius-cxcli-v0.1.1",
                local_source=None,
                chart_local_path="../../helm-charts/nccl-test",
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"incorrectly pinned app sources"):
        verify_catalog(catalog_path=catalog_path, release_ref="nebius-cxcli-v0.1.1")


def test_verify_wheel_reads_bundled_sources_while_archive_is_open(tmp_path: Path) -> None:
    wheel_path = tmp_path / "nebius_cxcli-0.1.1-py3-none-any.whl"
    bundled_name = "nebius_cxcli-0.1.1.data/data/nebius_cxcli/component_sources.yaml"
    bundled_settings_name = (
        "nebius_cxcli-0.1.1.data/data/nebius_cxcli/component_cli_settings.yaml"
    )
    payload = _catalog_payload(
        "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=nebius-cxcli-v0.1.1",
        local_source=None,
    )
    with zipfile.ZipFile(wheel_path, "w") as zf:
        zf.writestr(bundled_name, yaml.safe_dump(payload, sort_keys=False))
        zf.writestr(bundled_settings_name, yaml.safe_dump({}, sort_keys=False))

    verify_wheel(wheel_path=wheel_path, release_ref="nebius-cxcli-v0.1.1")


def test_verify_wheel_bundle_allows_app_chart_without_portable_source(tmp_path: Path) -> None:
    wheel_path = tmp_path / "nebius_cxcli-0.1.1-py3-none-any.whl"
    bundled_name = "nebius_cxcli-0.1.1.data/data/nebius_cxcli/component_sources.yaml"
    bundled_settings_name = (
        "nebius_cxcli-0.1.1.data/data/nebius_cxcli/component_cli_settings.yaml"
    )
    payload = _catalog_payload(
        "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main",
        local_source=None,
    )
    payload["components"]["apps"] = {"local-only": {"source": {}}}
    with zipfile.ZipFile(wheel_path, "w") as zf:
        zf.writestr(bundled_name, yaml.safe_dump(payload, sort_keys=False))
        zf.writestr(bundled_settings_name, yaml.safe_dump({}, sort_keys=False))

    verify_wheel_bundle(wheel_path=wheel_path)


def test_verify_wheel_bundle_rejects_bundled_local_sources(tmp_path: Path) -> None:
    wheel_path = tmp_path / "nebius_cxcli-0.1.1-py3-none-any.whl"
    bundled_name = "nebius_cxcli-0.1.1.data/data/nebius_cxcli/component_sources.yaml"
    bundled_settings_name = (
        "nebius_cxcli-0.1.1.data/data/nebius_cxcli/component_cli_settings.yaml"
    )
    payload = _catalog_payload(
        "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main",
        chart_local_path="../../helm-charts/nccl-test",
    )
    with zipfile.ZipFile(wheel_path, "w") as zf:
        zf.writestr(bundled_name, yaml.safe_dump(payload, sort_keys=False))
        zf.writestr(bundled_settings_name, yaml.safe_dump({}, sort_keys=False))

    with pytest.raises(ValueError, match=r"local source entries"):
        verify_wheel_bundle(wheel_path=wheel_path)


def test_release_catalog_help_mentions_settings_bundle() -> None:
    assert "component sources and CLI settings" in _build_parser().format_help()
