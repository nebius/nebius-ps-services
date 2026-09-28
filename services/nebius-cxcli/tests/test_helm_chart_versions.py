from __future__ import annotations

import pytest

from nebius_cxcli import cli
from nebius_cxcli.flux_render import render_flux
from test_deployment_campaign import paths


@pytest.mark.parametrize("version", ["latest", "*", "1.2", "1.2.x", ">=1.2.3", "^1.2.3", "~1.2.3"])
def test_catalog_validation_rejects_moving_chart_selectors(version):
    resolved, issue = cli._catalog_chart_validation_version(configured_version=version)
    assert resolved == ""
    assert issue and "exact" in issue


@pytest.mark.parametrize("version", ["latest", "*", "1.2", "1.2.x", ">=1.2.3", "^1.2.3", "~1.2.3"])
def test_render_rejects_moving_chart_selectors_before_writing_bundle(tmp_path, version):
    project = paths(tmp_path)
    config = {
        "apps": {
            "charts": [
                {
                    "id": "example",
                    "enabled": True,
                    "repo": "https://charts.example.invalid",
                    "chart": "example",
                    "version": version,
                    "namespace": "example",
                    "values": {},
                }
            ]
        }
    }
    with pytest.raises(ValueError, match="exact"):
        render_flux(config, project)
    assert not list(project.flux_dir.rglob("*.yaml"))


@pytest.mark.parametrize("version", ["1.2.3", "v25.10.0", "1.2.3-rc.1", "1.2.3+build.4"])
def test_catalog_validation_preserves_exact_chart_versions(version):
    assert cli._catalog_chart_validation_version(configured_version=version) == (version, None)
