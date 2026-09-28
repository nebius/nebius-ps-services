"""Effective Flux publication survives interruption without reinterpreting admission."""

import base64
import copy
import json
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli, compatibility_execution, deployment_cli
from nebius_cxcli.compatibility_adapters import receipt
from nebius_cxcli.compatibility_execution import admit_compatibility, freeze_compatibility
from nebius_cxcli.deployment_applications import ApplicationJournal, target_bundle_digest
from nebius_cxcli.deployment_effective_inputs import prepare_recovered_application_metadata
from nebius_cxcli.deployment_recovery import capture_execution_cache, restore_execution_cache
from nebius_cxcli.deployment_resolution import publish_application_inputs
from nebius_cxcli.deployment_state import DeploymentGeneration
from nebius_cxcli.frozen_catalog import freeze_catalog
from test_deployment_application_publication import bundles
from test_deployment_campaign import paths


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    local, generation, _ = bundles(tmp_path / "original")
    payload = {"infra": {"components": []}, "apps": {"charts": []}}
    subject = {"kind": "helm", "component_id": "soperator", "enabled": True}
    monkeypatch.setattr(
        compatibility_execution, "selected_inventory", lambda *a, **kw: [dict(subject)]
    )

    def evidence(subject, current):
        # Keep the production receipt and freeze/admit comparison; isolate only
        # external release acquisition. Its evidence depends on actual bytes.
        return [], [
            receipt(
                subject,
                "frozen_soperator_release_contract",
                (current.flux_dir / "targets/one/values.yaml").read_text(),
                reason="fixture frozen source contract",
            )
        ]

    monkeypatch.setattr(compatibility_execution, "soperator_evidence", evidence)
    monkeypatch.setattr(
        compatibility_execution,
        "assess",
        lambda *a, **kw: {"rows": [], "admitted": True},
    )
    frozen = freeze_compatibility(payload, local)
    generation = replace(
        generation,
        manifest={
            **generation.manifest,
            "schema": "nebius-cxcli-generated/v2",
            "execution": {"backend": {}},
            "runtime_config": payload,
            "render": {"inputs": freeze_catalog(payload), "compatibility": frozen},
        },
    )
    manifest = generation.manifest_for_paths(local)
    (local.generated_dir / "nebius-cxcli-manifest.json").write_text(json.dumps(manifest))
    name = "flux/targets/one/values.yaml"
    desired = replace(
        generation,
        files={
            **generation.files,
            name: base64.b64encode(
                base64.b64decode(generation.files[name]).replace(b"accounting", b"resolved")
            ).decode(),
        },
    )
    return local, generation, desired, manifest


def admit(local, manifest):
    return admit_compatibility(
        manifest["runtime_config"],
        local,
        manifest["render"]["compatibility"],
        terraform_validated=False,
    )


def hydrate(local, desired):
    (local.flux_dir / "targets/one/values.yaml").write_bytes(
        base64.b64decode(desired.files["flux/targets/one/values.yaml"])
    )


def bind(local, authored, desired):
    journal = ApplicationJournal(
        local.reports_dir / "deployment-applications.json",
        generation=authored.identity,
        selected=["one"],
    )
    journal.bind(
        "one",
        {"cluster_id": "cluster", "kubernetes_uid": "uid"},
        target_bundle_digest(desired, "one"),
    )
    return journal


def test_effective_publication_cache_restore_passes_real_receipt_comparison(inputs, tmp_path):
    local, authored, desired, manifest = inputs
    original = authored.as_payload()
    published = publish_application_inputs(
        cli, desired, local, current=authored, selected=["one"], assert_authority=lambda: None
    )
    assert admit(local, published)["admitted"]
    with pytest.raises(ValueError, match="compatibility inputs changed"):
        admit(local, manifest)
    cache = capture_execution_cache(local.repo_root)
    restored = paths(tmp_path / "restored")
    restore_execution_cache(restored.repo_root, cache)
    restored_manifest = cli.load_generated_manifest(restored.generated_dir)
    assert admit(restored, restored_manifest)["admitted"]
    actual = DeploymentGeneration.capture(restored, restored_manifest)
    assert actual.files == desired.files
    assert actual.manifest["runtime_config"] == authored.manifest["runtime_config"]
    assert authored.as_payload() == original
    # A pre-binding interruption needs no journal or output resolution.
    assert (
        prepare_recovered_application_metadata(
            cli,
            authored,
            restored,
            restored_manifest,
            selected=["one"],
            assert_authority=lambda: None,
        )
        == restored_manifest
    )


def test_selected_publication_preserves_other_effective_target(inputs):
    local, authored, desired, manifest = inputs
    other = local.flux_dir / "targets/two/kustomization.yaml"
    other.write_text("resources: []\n# another target's effective publication\n")
    current = DeploymentGeneration.capture(local, manifest)
    published = publish_application_inputs(
        cli, desired, local, current=current, selected=["one"], assert_authority=lambda: None
    )
    assert admit(local, published)["admitted"]
    actual = DeploymentGeneration.capture(local, published)
    assert (
        actual.files["flux/targets/two/kustomization.yaml"]
        == current.files["flux/targets/two/kustomization.yaml"]
    )
    assert actual.files["infra/main.tf"] == authored.files["infra/main.tf"]


def test_authenticated_metadata_repair_preserves_authored_generation_and_checkpoint(inputs):
    local, authored, desired, manifest = inputs
    hydrate(local, desired)
    journal = bind(local, authored, desired)
    before = journal.path.read_bytes()
    with pytest.raises(ValueError, match="compatibility inputs changed"):
        admit(local, manifest)
    updated = prepare_recovered_application_metadata(
        cli, authored, local, manifest, selected=["one"], assert_authority=lambda: None
    )
    assert admit(local, updated)["admitted"]
    assert journal.path.read_bytes() == before
    assert DeploymentGeneration.capture(local, updated).files == desired.files
    assert updated["runtime_config"] == manifest["runtime_config"]
    assert authored.manifest["render"]["compatibility"] == manifest["render"]["compatibility"]


@pytest.mark.parametrize(
    "drift",
    ["unbound", "values", "infra", "dashboard", "other", "unreferenced", "kustomize", "lease"],
)
def test_repair_rejects_unproved_changes_without_rewriting_metadata(inputs, drift):
    local, authored, desired, manifest = inputs
    hydrate(local, desired)
    if drift != "unbound":
        bind(local, authored, desired)
    if drift == "values":
        (local.flux_dir / "targets/one/values.yaml").write_text("foreign resource")
    elif drift == "infra":
        (local.infra_dir / "main.tf").write_text("foreign infrastructure")
    elif drift == "dashboard":
        (local.generated_dir / "grafana_dashboards/one/fixture.json").write_text("{}")
    elif drift == "other":
        (local.flux_dir / "targets/two/kustomization.yaml").write_text("resources: []\n# changed")
    elif drift == "unreferenced":
        (local.flux_dir / "targets/one/unreferenced.yaml").write_text("foreign data")
    elif drift == "kustomize":
        with (local.flux_dir / "targets/one/kustomization.yaml").open("a") as stream:
            stream.write("namePrefix: foreign-\n")

    def authority():
        if drift == "lease":
            raise RuntimeError("lease lost")

    before = (local.generated_dir / "nebius-cxcli-manifest.json").read_bytes()
    with pytest.raises((RuntimeError, ValueError)):
        prepare_recovered_application_metadata(
            cli, authored, local, manifest, selected=["one"], assert_authority=authority
        )
    assert (local.generated_dir / "nebius-cxcli-manifest.json").read_bytes() == before


def test_unbound_unchanged_install_does_not_need_outputs_or_rebinding(inputs, monkeypatch):
    local, authored, _, manifest = inputs
    monkeypatch.setattr(
        "nebius_cxcli.deployment_effective_inputs.bind_generation_compatibility",
        lambda *a: pytest.fail("partial install must not resolve unavailable outputs"),
    )
    assert (
        prepare_recovered_application_metadata(
            cli, authored, local, manifest, selected=["one"], assert_authority=lambda: None
        )
        == manifest
    )


def test_stage_metadata_keeps_its_own_strict_admission(inputs, monkeypatch):
    local, authored, desired, manifest = inputs
    hydrate(local, desired)
    stage = copy.deepcopy(manifest)
    stage["render"]["stage"] = "grow"
    monkeypatch.setattr(
        "nebius_cxcli.deployment_effective_inputs.bind_generation_compatibility",
        lambda *a: pytest.fail("stage evidence cannot be reinterpreted"),
    )
    assert (
        prepare_recovered_application_metadata(
            cli, authored, local, stage, selected=["one"], assert_authority=lambda: None
        )
        == stage
    )
    with pytest.raises(ValueError, match="compatibility inputs changed"):
        admit(local, stage)


def test_authored_replay_does_not_reuse_effective_report_for_changed_files(inputs, monkeypatch):
    local, authored, desired, manifest = inputs
    hydrate(local, desired)
    monkeypatch.setattr(
        deployment_cli, "use_generation_soperator_releases", lambda *a, **kw: nullcontext({})
    )
    calls = []

    def admission(config, replay, frozen, **kwargs):
        calls.append(True)
        assert replay.infra_dir == local.infra_dir
        assert (
            DeploymentGeneration.capture(
                replace(replay, infra_dir=replay.generated_dir / "infra"), manifest
            ).files
            == authored.files
        )
        return {"proof": "authored"}

    runner = SimpleNamespace(
        generation=authored,
        paths=local,
        manifest=manifest,
        config=manifest["runtime_config"],
        compatibility_report={"proof": "effective"},
        cli=SimpleNamespace(
            _terraform_runtime_env=lambda _: {},
            _config_has_enabled_infra_components=lambda _: False,
            _required_runtime_component_output_specs=lambda _: [],
            admit_compatibility=admission,
        ),
    )
    assert deployment_cli._CliDeploymentExecutor._authored_compatibility_replay(runner) == {
        "proof": "authored"
    }
    assert calls == [True]


def test_publication_interruption_recovers_matching_files_and_metadata(inputs, monkeypatch):
    from nebius_cxcli import project_bundle_transaction

    local, authored, desired, _ = inputs
    transaction = project_bundle_transaction.ProjectBundleTransaction

    def failpoint(name):
        if name == "after-commit":
            raise OSError("interrupted publication")

    monkeypatch.setattr(
        project_bundle_transaction,
        "ProjectBundleTransaction",
        lambda project: transaction(project, failpoint=failpoint),
    )
    with pytest.raises(OSError, match="interrupted publication"):
        publish_application_inputs(
            cli, desired, local, current=authored, selected=["one"], assert_authority=lambda: None
        )
    assert transaction(local.project_dir).recover()
    updated = json.loads((local.generated_dir / "nebius-cxcli-manifest.json").read_text())
    assert admit(local, updated)["admitted"]
    assert DeploymentGeneration.capture(local, updated).files == desired.files


def test_unselected_file_race_cannot_publish_stale_composite_metadata(inputs, monkeypatch):
    from nebius_cxcli import deployment_resolution

    local, authored, desired, manifest = inputs
    binder = deployment_resolution.bind_generation_compatibility

    def race(*args):
        bound = binder(*args)
        (local.infra_dir / "main.tf").write_text("concurrent infrastructure")
        return bound

    monkeypatch.setattr(deployment_resolution, "bind_generation_compatibility", race)
    with pytest.raises(RuntimeError, match="generation changed"):
        publish_application_inputs(
            cli, desired, local, current=authored, selected=["one"], assert_authority=lambda: None
        )
    assert (local.flux_dir / "targets/one/values.yaml").read_bytes() == base64.b64decode(
        authored.files["flux/targets/one/values.yaml"]
    )
    assert json.loads((local.generated_dir / "nebius-cxcli-manifest.json").read_text()) == manifest


def test_recovery_uses_existing_stable_soperator_projection(inputs):
    import yaml

    from nebius_cxcli.flux_ops import stable_soperator_documents
    from test_soperator_flux_sources import _outer_bundle, _staged_contract

    local, authored, _, _ = inputs
    outer = yaml.safe_load(_outer_bundle())
    graph = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "nebius-cxcli-soperator-release-graph", "namespace": "flux-system"},
        "data": {"graph.json": json.dumps(_staged_contract())},
    }
    bundle = local.flux_dir / "targets/one/soperator.yaml"
    bundle.write_text(yaml.safe_dump_all([outer, graph]))
    kustomization = local.flux_dir / "targets/one/kustomization.yaml"
    kustomization.write_text("resources: [values.yaml, soperator.yaml]\n")
    manifest = authored.manifest_for_paths(local)
    authored = DeploymentGeneration.capture(local, manifest)
    # Output hydration reserializes the raw umbrella while its checkpoint uses
    # the existing permanent executor transformation (disableWait etc.).
    bundle.write_text(yaml.safe_dump_all([outer, graph], sort_keys=False))
    current = DeploymentGeneration.capture(local, manifest)
    assert current.files != authored.files
    assert stable_soperator_documents([outer, graph], _staged_contract()["releases"]) != [
        outer,
        graph,
    ]
    bind(local, authored, current)
    updated = prepare_recovered_application_metadata(
        cli, authored, local, manifest, selected=["one"], assert_authority=lambda: None
    )
    assert admit(local, updated)["admitted"]
    assert DeploymentGeneration.capture(local, updated).files == current.files
