"""Publication locks protect artifact snapshots without pinning local execution."""

from dataclasses import replace
from pathlib import Path

import pytest

from nebius_cxcli import deployment_cli
from nebius_cxcli.deployment_local import DeploymentLocalLock, LocalExecutionOwner
from nebius_cxcli.paths import resolve_project_paths
from test_deployment_local import settings


def test_render_can_publish_during_execution_but_publishers_serialize(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    paths = resolve_project_paths(tmp_path / "tenant" / "project" / "config.yaml")
    with LocalExecutionOwner(settings=settings(), operation_id="deployment") as owner:
        with deployment_cli.render_publication_lock(config={}, paths=paths):
            owner.assert_held()
            with (
                pytest.raises(RuntimeError, match="Another deployment"),
                deployment_cli.render_publication_lock(config={}, paths=paths),
            ):
                pass
        with deployment_cli.render_publication_lock(config={}, paths=paths):
            owner.assert_held()


def test_publication_lock_is_shared_by_path_alias_and_released_on_error(tmp_path):
    paths = resolve_project_paths(tmp_path / "tenant" / "project" / "config.yaml")
    with pytest.raises(ValueError), deployment_cli.render_publication_lock(config={}, paths=paths):
        alias = replace(paths, project_dir=paths.project_dir / "alias" / "..")
        (paths.project_dir / "alias").mkdir()
        with (
            pytest.raises(RuntimeError, match="Another deployment"),
            deployment_cli.render_publication_lock(config={}, paths=alias),
        ):
            pass
        raise ValueError("interrupted")
    with DeploymentLocalLock(paths.project_dir / ".nebius-cxcli" / "render.lock"):
        pass
