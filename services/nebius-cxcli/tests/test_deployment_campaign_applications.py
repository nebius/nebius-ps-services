"""Campaign prerequisites cannot rewrite the parent's sealed configuration."""

import base64
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli, deployment_cli, deployment_target
from nebius_cxcli.deployment_state import DeploymentGeneration
from nebius_cxcli.frozen_catalog import freeze_catalog
from nebius_cxcli.render import project_generation_snapshot_sha256
from test_deployment_campaign import paths
from test_deployment_plan import config


@pytest.mark.parametrize("interrupt", [False, True])
def test_prerequisites_use_resolved_inputs_without_changing_parent(
    tmp_path, monkeypatch, interrupt
):
    payload = config()
    monkeypatch.setattr(
        "nebius_cxcli.compatibility_execution.freeze_compatibility",
        lambda *a: {"report": {"fixture": "constraints-adapter"}},
    )
    # This fixture isolates prerequisite publication from upstream artifact acquisition.
    monkeypatch.setattr(
        "nebius_cxcli.deployment_resolution.use_generation_soperator_releases",
        lambda *a, **kw: nullcontext(),
    )
    local = paths(tmp_path)
    frozen = DeploymentGeneration(
        {
            "runtime_config": payload,
            "render": {"inputs": freeze_catalog(payload)},
            "deploy": {
                "targets": [
                    {
                        "target_ref": "cluster",
                        "instance_id": "cluster",
                        "component_id": "mk8s",
                        "access": "external",
                        "cluster_id_output_name": "cluster_id",
                        "component_output_ref": "mk8s.cluster",
                        "flux_dir": "flux/targets/cluster",
                    }
                ]
            },
        },
        {"flux/targets/cluster/values.yaml": base64.b64encode(b"unresolved: true\n").decode()},
    )
    manifest = frozen.materialize(local)
    local.config_path.write_text(cli.render_updated_source_payload(payload))
    before = project_generation_snapshot_sha256(local)
    monkeypatch.setattr(
        cli, "_required_runtime_component_output_specs", lambda _, **kw: [{"id": "storage"}]
    )
    monkeypatch.setattr(cli, "mysterybox_eso_terraform_output_specs", lambda _: [])
    monkeypatch.setattr(cli, "nfs_csi_terraform_output_specs", lambda _: [])
    monkeypatch.setattr(cli, "_runtime_component_output_values", lambda *a, **kw: {"storage": "id"})

    def render(_config, staged, **_kwargs):
        (staged.flux_dir / "targets/cluster/values.yaml").write_text("resolved: true\n")

    monkeypatch.setattr(cli, "render_flux", render)
    observed = []

    def authority():
        assert project_generation_snapshot_sha256(local) == before

    def prepare(_cli, _config, target_paths, **kwargs):
        kwargs["assert_authority"]()
        assert (target_paths.flux_dir / "values.yaml").read_text() == "resolved: true\n"
        observed.append(target_paths.flux_dir)
        if interrupt:
            raise RuntimeError("prerequisite interrupted")

    def apply(_cli, target_paths, **kwargs):
        kwargs["assert_authority"]()
        assert target_paths.flux_dir == observed[0]
        observed.append(target_paths.flux_dir)

    monkeypatch.setattr(deployment_target, "prepare_application_runtime", prepare)
    monkeypatch.setattr(deployment_target, "apply_ordinary_bundle", apply)
    executor = deployment_cli._CliDeploymentExecutor(
        payload, local, manifest, options=deployment_cli.DeployOptions(), lease=SimpleNamespace()
    )
    executor.generation = frozen
    executor.plan = SimpleNamespace(target_ref="cluster")
    if interrupt:
        with pytest.raises(RuntimeError, match="prerequisite interrupted"):
            executor.prepare_campaign_applications(kube_env={}, assert_authority=authority)
    else:
        executor.prepare_campaign_applications(kube_env={}, assert_authority=authority)
    assert len(observed) == (1 if interrupt else 2)
    assert all(not directory.exists() for directory in observed)
    authority()
