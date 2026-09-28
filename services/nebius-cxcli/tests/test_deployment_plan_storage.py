"""Saved Terraform execution plans must not alter configuration authority."""

from types import SimpleNamespace

import pytest

from nebius_cxcli import cli, deployment_cli
from nebius_cxcli.deployment_recovery import capture_execution_cache
from nebius_cxcli.render import project_generation_snapshot_sha256
from nebius_cxcli.terraform_ops import _saved_plan_identity
from test_deployment_campaign import generation, paths
from test_deployment_plan import config


@pytest.mark.parametrize("replace_addresses", [(), ("module.example.resource",)])
def test_saved_plan_preserves_configuration_snapshot(tmp_path, monkeypatch, replace_addresses):
    payload = config()
    local = paths(tmp_path)
    manifest = generation(payload).materialize(local)
    local.config_path.write_text(cli.render_updated_source_payload(payload))
    before = project_generation_snapshot_sha256(local)
    saved = []

    def plan(directory, *, plan_file, **_kwargs):
        # Exercise the production confinement/ownership guard, not only a file-writing mock.
        _saved_plan_identity(directory, plan_file, create=True)
        plan_file.write_bytes(b"opaque Terraform execution plan")
        saved.append(plan_file)

    monkeypatch.setattr(cli, "terraform_plan", plan)
    monkeypatch.setattr(cli, "terraform_show_json", lambda *a, **kw: {"resource_changes": []})
    monkeypatch.setattr(cli, "_config_has_enabled_infra_components", lambda _: True)
    executor = deployment_cli._CliDeploymentExecutor(
        payload,
        local,
        manifest,
        options=deployment_cli.DeployOptions(),
        lease=SimpleNamespace(),
    )

    plan_file, _admission = executor._terraform_plan(replace_addresses=replace_addresses)

    assert project_generation_snapshot_sha256(local) == before
    assert not any(name.endswith(".tfplan") for name in capture_execution_cache(tmp_path))
    if replace_addresses:
        assert plan_file is None and not saved[0].exists()
    else:
        assert plan_file is not None
        assert plan_file.read_bytes() == b"opaque Terraform execution plan"
        assert plan_file.stat().st_mode & 0o777 == 0o600

    # Configuration changes still invalidate the exact snapshot.
    (local.infra_dir / "main.tf").write_text("changed configuration")
    assert project_generation_snapshot_sha256(local) != before
