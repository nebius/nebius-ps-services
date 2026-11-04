"""Stage preflight inspects the staged generation under strict target ownership."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli
from nebius_cxcli.config_model import to_runtime_payload
from nebius_cxcli.deployment_cli import DeployOptions, _CliDeploymentExecutor, _execution_paths
from nebius_cxcli.deployment_plan import DeploymentStage, DeploymentStageKind
from nebius_cxcli.deployment_state import DeploymentGeneration
from nebius_cxcli.terraform_ops import _saved_plan_identity
from test_deployment_campaign import paths
from test_deployment_source_projection import source_payload


@pytest.mark.parametrize("aliased", [False, True])
def test_execution_cache_saved_plan_accepts_temporary_root_alias(tmp_path, aliased):
    original = paths(tmp_path / "original")
    real_root = tmp_path / "temporary"
    real_root.mkdir(mode=0o700)
    directory = real_root
    if aliased:
        directory = tmp_path / "temporary-alias"
        directory.symlink_to(real_root, target_is_directory=True)
    scratch = _execution_paths(original, directory)
    scratch.infra_dir.mkdir(mode=0o700, parents=True)
    plan = scratch.infra_dir / "cleanup.tfplan"

    saved, identity = _saved_plan_identity(scratch.infra_dir, plan, create=True)

    assert saved == real_root.resolve() / "tenant/project/generated/infra/cleanup.tfplan"
    assert _saved_plan_identity(scratch.infra_dir, plan, create=False) == (saved, identity)


def test_staged_admission_binds_manifest_to_staged_target_paths(tmp_path, monkeypatch):
    final = paths(tmp_path)
    generated = final.project_dir / "staged-generated"
    staged = replace(
        final,
        generated_dir=generated,
        infra_dir=generated / "infra",
        flux_dir=generated / "flux",
        reports_dir=generated / "reports",
    )
    staged.infra_dir.mkdir(parents=True)
    (staged.flux_dir / "targets/cluster").mkdir(parents=True)
    (staged.flux_dir / "targets/cluster/expected.yaml").write_text("kind: ConfigMap\n")
    runtime = to_runtime_payload(source_payload())
    manifest = {
        "schema": "nebius-cxcli-generated/v3",
        "execution": {
            "backend": {},
            "targets": [
                {
                    "instance_id": "cluster",
                    "target_ref": "cluster",
                    "flux_dir": str(final.flux_dir / "targets/cluster"),
                    "access": "external",
                    "component_id": "mk8s",
                    "ownership": "managed",
                    "cluster_id_output_name": "cluster" + "_cluster_id",
                    "component_output_ref": "cluster" + ".cluster_id",
                }
            ],
            "required_component_outputs": [],
            "status_watchers": [],
            "validations": [],
        },
        "runtime_config": runtime,
        "paths": {"generated_dir": str(final.generated_dir)},
    }
    frozen = DeploymentGeneration.capture(staged, manifest)
    before = frozen.as_payload()
    executor = _CliDeploymentExecutor(runtime, final, manifest, options=DeployOptions(), lease=None)
    executor.generation = executor.source_generation = frozen
    executor.plan = SimpleNamespace(
        target_ref="cluster", stages=(DeploymentStage(DeploymentStageKind.TRANSITION, runtime),)
    )
    cleaned = []
    monkeypatch.setattr(
        cli,
        "_render_soperator_upgrade_admission",
        lambda **_kwargs: SimpleNamespace(
            staged_paths=staged,
            admitted_config=runtime,
            cleanup=lambda: cleaned.append(True),
        ),
    )
    monkeypatch.setattr(cli, "load_generated_manifest", lambda _path: manifest)

    class PreflightReached(Exception):
        pass

    def preflight(self):
        target = self.manifest["execution"]["targets"][0]
        actual = cli._manifest_target_flux_dir(paths=self.paths, target=target)
        assert actual == (staged.flux_dir / "targets/cluster").resolve()
        assert (actual / "expected.yaml").read_text() == "kind: ConfigMap\n"
        assert DeploymentGeneration.capture(staged, self.manifest) == frozen
        raise PreflightReached

    monkeypatch.setattr(_CliDeploymentExecutor, "preflight", preflight)
    with pytest.raises(PreflightReached):
        executor._admit_coordinated_stages(executor.plan)
    assert frozen.as_payload() == before
    assert cleaned == [True]
    assert manifest["execution"]["targets"][0]["flux_dir"] == str(
        final.flux_dir / "targets/cluster"
    )


@pytest.mark.parametrize("locator", ["../escape", "/escape", "flux/targets/foreign"])
def test_manifest_binding_cannot_bypass_target_path_ownership(tmp_path, locator):
    project = paths(tmp_path)
    generation = DeploymentGeneration(
        {
            "execution": {
                "targets": [
                    {
                        "target_ref": "cluster",
                        "flux_dir": locator,
                        "instance_id": "cluster",
                        "access": "external",
                        "component_id": "mk8s",
                        "ownership": "managed",
                        "cluster_id_output_name": "cluster" + "_cluster_id",
                        "component_output_ref": "cluster" + ".cluster_id",
                    }
                ],
                "required_component_outputs": [],
                "status_watchers": [],
                "validations": [],
                "backend": {},
            },
            "schema": "nebius-cxcli-generated/v3",
            "runtime_config": {"version": "v2"},
        },
        {},
    )
    with pytest.raises((ValueError, RuntimeError)):
        bound = generation.manifest_for_paths(project)
        cli._manifest_target_flux_dir(paths=project, target=bound["execution"]["targets"][0])
    assert not project.generated_dir.exists()
