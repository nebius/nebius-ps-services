"""Resolved execution bytes stay bound to admission across recovered renders."""

import base64
from dataclasses import replace
from types import SimpleNamespace

import pytest
from rich.console import Console

from nebius_cxcli.deployment_applications import target_bundle_digest
from nebius_cxcli.deployment_cli import _CliDeploymentExecutor
from nebius_cxcli.deployment_resolution import publish_application_inputs
from nebius_cxcli.deployment_state import DeploymentGeneration
from test_deployment_campaign import paths


def bundles(tmp_path):
    local = paths(tmp_path)
    files = {
        "flux/targets/one/kustomization.yaml": b"resources: [values.yaml]\n",
        "flux/targets/one/values.yaml": (
            b"apiVersion: v1\nkind: ConfigMap\nmetadata: {name: settings}\n"
            b"data: {members: 'accounting,controller,login,system'}\n"
        ),
        "flux/targets/two/kustomization.yaml": b"resources: []\n",
        "infra/main.tf": b"preserve infrastructure",
        "grafana_dashboards/one/fixture.json": b'{"uid":"frozen"}',
        "grafana_dashboards/two/fixture.json": b'{"uid":"other"}',
    }
    desired = DeploymentGeneration(
        {
            "deploy": {
                "targets": [
                    {"target_ref": ref, "flux_dir": f"flux/targets/{ref}"} for ref in ("one", "two")
                ]
            }
        },
        {name: base64.b64encode(content).decode() for name, content in files.items()},
    )
    manifest = desired.materialize(local)
    local.reports_dir.mkdir()
    (local.reports_dir / "receipt.json").write_text("preserve receipt")
    return local, desired, manifest


def test_recovery_publishes_admitted_bytes_without_mutable_renderer(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "nebius_cxcli.deployment_resolution.bind_generation_compatibility",
        lambda cli, generation, paths: generation,
    )
    local, desired, manifest = bundles(tmp_path)
    values = local.flux_dir / "targets/one/values.yaml"
    values.write_bytes(
        values.read_bytes().replace(b"accounting,controller", b"controller,accounting")
    )
    stale = local.flux_dir / "targets/one/stale.yaml"
    stale.write_text("stale render")
    other = local.flux_dir / "targets/two/kustomization.yaml"
    other.write_text("resources: []\n# retain another target's execution bytes\n")
    before_other = other.read_bytes()
    dashboard = local.generated_dir / "grafana_dashboards/one/fixture.json"
    dashboard.write_text('{"uid":"mutable"}')
    calls = []
    runner = SimpleNamespace(
        lease=SimpleNamespace(assert_held=lambda: calls.append("held")),
        paths=local,
        manifest=manifest,
        config={"mutable": "execution config must not feed another renderer"},
        _desired_application_generation=lambda **kwargs: desired,
        _application_journal=lambda: SimpleNamespace(
            payload={"selected": ["one"]},
            entry=lambda ref: {
                "status": "executing",
                "desiredBundle": target_bundle_digest(desired, ref),
            },
        ),
        cli=SimpleNamespace(
            progress_console=Console(),
            _refresh_flux_after_terraform_outputs=lambda *a, **k: pytest.fail("second renderer"),
        ),
    )
    _CliDeploymentExecutor._prepare_install_application_inputs(runner)
    actual = DeploymentGeneration.capture(local, manifest)
    assert target_bundle_digest(actual, "one") == target_bundle_digest(desired, "one")
    assert not stale.exists()
    assert other.read_bytes() == before_other
    assert dashboard.read_bytes() == b'{"uid":"frozen"}'
    assert (
        local.generated_dir / "grafana_dashboards/two/fixture.json"
    ).read_bytes() == b'{"uid":"other"}'
    assert (local.infra_dir / "main.tf").read_text() == "preserve infrastructure"
    assert (local.reports_dir / "receipt.json").read_text() == "preserve receipt"
    assert len(calls) == 3


@pytest.mark.parametrize("failure", ["lease", "changed", "symlink", "escape", "overlap", "missing"])
def test_publication_fails_closed_before_target_writes(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(
        "nebius_cxcli.deployment_resolution.bind_generation_compatibility",
        lambda cli, generation, paths: generation,
    )
    local, desired, manifest = bundles(tmp_path)
    current = DeploymentGeneration.capture(local, manifest)
    selected = ["one"]
    values = local.flux_dir / "targets/one/values.yaml"
    if failure == "changed":
        values.write_text("concurrent writer")
    elif failure == "symlink":
        values.unlink()
        values.symlink_to(local.infra_dir / "main.tf")
    elif failure == "escape":
        desired = replace(
            desired,
            files={
                **desired.files,
                "flux/targets/one/../../../infra/main.tf": base64.b64encode(b"escape").decode(),
            },
        )
    elif failure == "overlap":
        desired.manifest["deploy"]["targets"][1]["flux_dir"] = "flux"
    elif failure == "missing":
        selected = ["missing"]
    before = values.read_bytes()
    calls = []

    def held():
        calls.append("held")
        if failure == "lease" and len(calls) == 2:
            raise RuntimeError("lease lost before publication")

    with pytest.raises((RuntimeError, ValueError)):
        publish_application_inputs(
            SimpleNamespace(),
            desired,
            local,
            current=current,
            selected=selected,
            assert_authority=held,
        )
    assert values.read_bytes() == before
    assert (local.infra_dir / "main.tf").read_text() == "preserve infrastructure"
    assert (local.reports_dir / "receipt.json").read_text() == "preserve receipt"


@pytest.mark.parametrize("drift", [None, "values", "source"])
def test_restored_cache_rebinds_only_frozen_oci_identity(tmp_path, drift):
    import json

    import yaml

    from nebius_cxcli.application_compatibility import application_files, verify_application_files
    from nebius_cxcli.compatibility_artifacts import bind_flux_artifacts
    from nebius_cxcli.deployment_recovery import capture_execution_cache

    local, _, manifest = bundles(tmp_path)
    ordinary = local.flux_dir / "targets/one/ordinary"
    ordinary.mkdir()
    source = {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "HelmRepository",
        "metadata": {"name": "operator", "namespace": "flux-system"},
        "spec": {"type": "oci", "url": "oci://registry.test/charts"},
    }
    release = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "operator", "namespace": "flux-system"},
        "spec": {
            "values": {"enabled": True},
            "chart": {
                "spec": {
                    "chart": "operator",
                    "version": "1.0.0",
                    "sourceRef": {"kind": "HelmRepository", "name": "operator"},
                }
            },
        },
    }
    inputs = {
        "operator": {
            "reference": {
                "chart_repo": "oci://registry.test/charts",
                "chart_name": "operator",
                "chart_version": "1.0.0",
            },
            "oci_digest": "sha256:" + "a" * 64,
        }
    }

    def raw():
        (ordinary / "source.yaml").write_text(yaml.safe_dump(source, sort_keys=False))
        (ordinary / "release.yaml").write_text(yaml.safe_dump(release, sort_keys=False))

    raw()
    bind_flux_artifacts(local, inputs)
    manifest.update(runtime_config={})
    manifest["render"] = {
        "compatibility": {"chart_inputs": inputs},
        "application_files": application_files(local),
    }
    (local.generated_dir / "nebius-cxcli-manifest.json").write_text(json.dumps(manifest))
    # A lifecycle render checkpoint can hold native chart references while its
    # immutable manifest still records the already admitted digest binding.
    if drift == "source":
        source["spec"]["url"] = "oci://foreign.test/charts"
    elif drift == "values":
        release["spec"]["values"]["enabled"] = False
    raw()
    cache = capture_execution_cache(local.repo_root)
    restored = paths(tmp_path / "restored")
    runner = SimpleNamespace(
        paths=restored,
        _prepare_runtime_inputs=lambda: None,
        cli=SimpleNamespace(
            load_generated_manifest=lambda root: json.loads(
                (root / "nebius-cxcli-manifest.json").read_text()
            ),
            _config_has_enabled_infra_components=lambda _: False,
            _required_runtime_component_output_specs=lambda _: [],
        ),
    )
    if drift == "source":
        with pytest.raises(ValueError, match="no exact frozen chart identity"):
            _CliDeploymentExecutor.restore_execution(runner, cache)
    else:
        _CliDeploymentExecutor.restore_execution(runner, cache)
        if drift:
            with pytest.raises(ValueError, match="Frozen application files changed"):
                verify_application_files(restored, runner.manifest)
        else:
            verify_application_files(restored, runner.manifest)
