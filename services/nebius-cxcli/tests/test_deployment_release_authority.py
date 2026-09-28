"""Deploy admission and execution retain generation authority in the release engine."""

import base64
import copy
import json
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli, deployment_cli, soperator_release_artifacts
from nebius_cxcli import soperator_release_resolver as resolver
from nebius_cxcli.deployment_plan import DeploymentAction, DeploymentStage, DeploymentStageKind
from nebius_cxcli.deployment_state import DeploymentGeneration
from soperator_fixtures import sample_snapshot
from test_deployment_campaign import paths
from test_deployment_plan import config


@pytest.fixture
def release_case(tmp_path, monkeypatch):
    expected = sample_snapshot(target_ref="cluster")
    changed_chart = replace(expected.umbrella, package_sha256="sha256:" + "f" * 64)
    moved = resolver.seal_soperator_release_snapshot(
        replace(expected, charts={"umbrella": changed_chart}, snapshot_sha256="")
    )
    payload = config()
    payload["client_info"]["notifications"] = {}
    monkeypatch.setattr(cli, "_ensure_project_auth_identity", lambda **_: None)
    payload["apps"]["charts"][0].update(
        version=expected.release, repo=expected.chart_oci_url("umbrella")
    )
    frozen = DeploymentGeneration(
        {"runtime_config": copy.deepcopy(payload)},
        {
            "reports/soperator-release-snapshot-cluster.json": base64.b64encode(
                json.dumps(expected.canonical_payload()).encode()
            ).decode()
        },
    )
    local = paths(tmp_path)
    local.config_path.parent.mkdir(parents=True, exist_ok=True)
    local.config_path.write_text(cli.render_updated_source_payload(payload))
    executor = deployment_cli._CliDeploymentExecutor(
        payload,
        local,
        frozen.manifest,
        options=deployment_cli.DeployOptions(job_policy="wait-to-finish"),
        lease=None,
    )
    executor.generation = frozen
    executor.plan = SimpleNamespace(
        action=DeploymentAction.RECONCILE,
        target_ref="cluster",
        target_release=expected.release,
        changed_fields=("values",),
        stages=(DeploymentStage(DeploymentStageKind.RECONCILE, payload),),
    )
    target = SimpleNamespace(target_ref="cluster", selector="soperator:cluster")
    monkeypatch.setattr(
        cli, "_resolve_soperator_command_target", lambda *a, **kw: (target, None, False)
    )
    monkeypatch.setattr(cli, "_source_helm_chart_row", lambda data, _: data["apps"]["charts"][0])
    monkeypatch.setattr(executor, "_terraform_plan", lambda **kw: (None, {"resource_changes": []}))
    monkeypatch.setattr(cli, "_preflight_soperator_install_checks", lambda *a: None)
    monkeypatch.setattr(cli, "_resolve_selected_deploy_targets", lambda *a, **kw: [{}])
    monkeypatch.setattr(cli, "_paths_for_target_flux_dir", lambda *a: local)
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *a, **kw: {
            cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "fixture-cluster",
            cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "fixture-context",
        },
    )
    monkeypatch.setattr(cli, "_live_soperator_release_for_reconcile", lambda **kw: expected.release)
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **kw: "fixture-uid")
    monkeypatch.setattr(cli, "_read_soperator_slurm_cluster_journal", lambda **kw: None)
    monkeypatch.setattr(cli, "load_active_soperator_release_intent", lambda **kw: None)
    monkeypatch.setattr(
        resolver,
        "SoperatorReleaseIdentityLedger",
        lambda root: SimpleNamespace(locked=lambda metadata: nullcontext()),
    )
    monkeypatch.setattr(resolver, "_load_recent_release_snapshot", lambda *a, **kw: moved)
    monkeypatch.setattr(resolver, "_retain_release_snapshot", lambda *a, **kw: None)
    monkeypatch.setattr(
        resolver,
        "frozen_soperator_release_from_snapshot",
        lambda snapshot, **kw: resolver.FrozenSoperatorRelease(object(), object(), snapshot),
    )
    verified = []

    def verify(snapshot, *args, **kwargs):
        if snapshot != expected:
            raise ValueError("official OCI chart helm-nfs-server differs from release source")
        verified.append(snapshot)

    monkeypatch.setattr(soperator_release_artifacts, "verify_soperator_release_artifacts", verify)

    return expected, executor, verified, local


@pytest.mark.parametrize("dry_run", [True, False])
def test_deploy_release_reaches_common_engine_with_generation_snapshot(
    release_case, monkeypatch, dry_run
):
    expected, executor, verified, local = release_case

    class SourceInspectionReached(Exception):
        pass

    def inspect_source(*args, **kwargs):
        raise SourceInspectionReached

    monkeypatch.setattr(cli, "inspect_soperator_release_contract", inspect_source)
    with pytest.raises(SourceInspectionReached):
        if dry_run:
            executor.admit(executor.plan)
        else:
            executor._release(dry_run=False)
    assert verified == [expected, expected]
    assert (
        resolver.current_frozen_soperator_release(expected.release, target_ref=expected.target_ref)
        is None
    )
    assert not list(local.project_dir.rglob("*snapshot*.json"))


@pytest.mark.parametrize("corruption", ["digest", "content"])
def test_bound_snapshot_conflict_is_rejected_before_disk_fallback(tmp_path, corruption):
    expected = sample_snapshot(target_ref="cluster")
    bound = (
        expected
        if corruption == "digest"
        else replace(expected, archive_sha256="sha256:" + "f" * 64)
    )
    digest = "sha256:" + "f" * 64 if corruption == "digest" else expected.snapshot_sha256
    with (
        resolver.use_frozen_soperator_release(SimpleNamespace(snapshot=bound)),
        pytest.raises(ValueError, match="snapshot.*digest"),
    ):
        resolver.freeze_soperator_release(
            expected.release,
            target_ref=expected.target_ref,
            cache_root=tmp_path,
            snapshot_sha256=digest,
        )


def test_bound_snapshot_does_not_bypass_downgrade_rejection(tmp_path):
    snapshot = sample_snapshot(target_ref="cluster")
    with (
        resolver.use_frozen_soperator_release(SimpleNamespace(snapshot=snapshot)),
        pytest.raises(ValueError, match="downgrade"),
    ):
        resolver.freeze_soperator_release(
            snapshot.release,
            current_release="99.0.0",
            target_ref=snapshot.target_ref,
            cache_root=tmp_path,
            snapshot_sha256=snapshot.snapshot_sha256,
        )


@pytest.mark.parametrize("matching", [True, False])
def test_completed_child_must_match_parent_snapshot_before_completion(
    release_case, monkeypatch, matching
):
    expected, executor, verified, _ = release_case
    active_snapshot = (
        expected
        if matching
        else resolver.seal_soperator_release_snapshot(
            replace(expected, archive_sha256="sha256:" + "f" * 64, snapshot_sha256="")
        )
    )
    monkeypatch.setattr(
        cli,
        "load_active_soperator_release_intent",
        lambda **kw: (SimpleNamespace(target_release=expected.release), active_snapshot),
    )
    observed = []
    completed = []

    def anchor(**kwargs):
        observed.append(True)
        return "complete"

    monkeypatch.setattr(cli, "soperator_operation_anchor_status", anchor)
    monkeypatch.setattr(
        cli, "complete_soperator_release_intent", lambda **kw: completed.append(True)
    )
    if matching:
        executor._release(dry_run=False)
        assert observed == completed == [True]
    else:
        with pytest.raises(cli.SoperatorSafetyPauseError, match="snapshot identities differ"):
            executor._release(dry_run=False)
        assert observed == completed == []
    assert verified == [expected]
    assert (
        resolver.current_frozen_soperator_release(expected.release, target_ref=expected.target_ref)
        is None
    )
