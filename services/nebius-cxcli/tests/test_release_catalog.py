from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
import yaml

from nebius_cxcli.release_catalog import render_release_catalog, verify_catalog, verify_wheel


def _catalog_payload(
    portable_source: str, *, local_source: str | None = "../../platform-infra/modules/mk8s"
) -> dict[str, object]:
    return {
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


def test_verify_catalog_rejects_external_main_ref(tmp_path: Path) -> None:
    catalog_path = tmp_path / "component_sources.yaml"
    catalog_path.write_text(
        yaml.safe_dump(
            _catalog_payload("git::https://github.com/example/infra.git//modules/mk8s?ref=main"),
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"incorrectly pinned module sources"):
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

    with pytest.raises(ValueError, match=r"incorrectly pinned module sources"):
        verify_catalog(catalog_path=catalog_path, release_ref="nebius-cxcli-v0.1.1")


def test_verify_wheel_reads_bundled_sources_while_archive_is_open(tmp_path: Path) -> None:
    wheel_path = tmp_path / "nebius_cxcli-0.1.1-py3-none-any.whl"
    bundled_name = "nebius_cxcli-0.1.1.data/data/nebius_cxcli/component_sources.yaml"
    payload = _catalog_payload(
        "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=nebius-cxcli-v0.1.1",
        local_source=None,
    )
    with zipfile.ZipFile(wheel_path, "w") as zf:
        zf.writestr(bundled_name, yaml.safe_dump(payload, sort_keys=False))

    verify_wheel(wheel_path=wheel_path, release_ref="nebius-cxcli-v0.1.1")
