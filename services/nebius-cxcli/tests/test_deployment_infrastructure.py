from __future__ import annotations

import copy
import json
from types import SimpleNamespace as NS

import pytest
from nebius.api.nebius.mk8s.v1 import ClusterStatus, NodeGroupStatus

from nebius_cxcli import cli
from nebius_cxcli import deployment_infrastructure as infra
from nebius_cxcli.cluster_config import cluster_rows
from nebius_cxcli.deploy_targets import flux_target_dir
from nebius_cxcli.deploy_validation_report import build_deploy_validation_report
from nebius_cxcli.deployment_reports import publish_deployment_reports
from nebius_cxcli.deployment_target import deploy_application_target
from test_deployment_campaign import paths


def config():
    return {
        "client_info": {
            "client_name": "example",
            "nebius": {"project_id": "project-test", "region_id": "eu-north2"},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster",
                    "enabled": True,
                    "inputs": {
                        "cluster": {"name": "cluster", "public_endpoint": False},
                        "node_groups": {"cpu": {"name": "cpu", "node_count": 2, "gpu": False}},
                    },
                    "observability": {"enabled": False},
                }
            ]
        },
        "apps": {"charts": []},
        "version": "v2",
    }


TARGET = {
    "target_ref": "cluster",
    "instance_id": "cluster",
    "ownership": "managed",
    "component_id": "mk8s",
    "cluster_id_output_name": "cluster_id",
    "component_output_ref": "cluster.cluster_id",
    "access": "external",
    "flux_dir": "flux/targets/cluster",
}
VALIDATION = {
    "kind": "mk8s_cluster_smoke",
    "target_ref": "cluster",
    "required": True,
    "name": "MK8s node inventory smoke",
    "report_file": "cluster-smoke.json",
}


def live():
    cluster = NS(
        metadata=NS(id="cluster-id", parent_id="project-test", name="cluster"),
        status=NS(
            state=ClusterStatus.State.RUNNING,
            reconciling=False,
            control_plane=NS(endpoints=NS(private_endpoint="private-test", public_endpoint="")),
        ),
    )
    group = NS(
        metadata=NS(id="group-id", parent_id="cluster-id", name="cpu"),
        status=NS(
            state=NodeGroupStatus.State.RUNNING,
            reconciling=False,
            target_node_count=2,
            ready_node_count=2,
            node_count=2,
            outdated_node_count=0,
        ),
    )
    return cluster, group


def snapshot(cluster, groups, payload=None):
    return infra._ready_snapshot(
        cluster,
        groups,
        cluster_id="cluster-id",
        project_id="project-test",
        component=(payload or config())["infra"]["components"][0],
        target_ref="cluster",
    )


@pytest.mark.parametrize("name", [None, ""])
def test_default_group_name_matches_terraform_cluster_prefix(name):
    payload = config()
    payload["infra"]["components"][0]["inputs"]["node_groups"]["cpu"]["name"] = name
    cluster, group = live()
    group.metadata.name = "cluster-cpu"
    assert snapshot(cluster, [group], payload)["passed"]


def test_duplicate_configured_group_names_cannot_collapse_inventory():
    payload = config()
    groups = payload["infra"]["components"][0]["inputs"]["node_groups"]
    groups["other"] = dict(groups["cpu"])
    cluster, group = live()
    with pytest.raises(RuntimeError, match="ambiguous node-group names"):
        snapshot(cluster, [group], payload)


def test_private_infrastructure_deploy_never_connects_to_kubernetes(tmp_path, monkeypatch):
    local = paths(tmp_path)
    cluster, group = live()
    proof = snapshot(cluster, [group])
    monkeypatch.setattr(infra, "wait_infrastructure_ready", lambda **kw: proof)
    monkeypatch.setattr(cli, "terraform_output_raw", lambda *a, **kw: "cluster-id")
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda *a: {})

    def no_kubernetes(*a, **kw):
        pytest.fail("Infrastructure-only deployment attempted Kubernetes access")

    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", no_kubernetes)
    result = deploy_application_target(
        cli,
        config(),
        local,
        {**TARGET, "flux_dir": str(flux_target_dir(local, "cluster"))},
        deploy_validations=[VALIDATION],
        deployment_lease=NS(assert_held=lambda: None),
        on_identity=no_kubernetes,
        on_inputs=no_kubernetes,
    )
    assert result["infrastructure"] == proof
    assert not result.get("identity")
    report = build_deploy_validation_report([VALIDATION], reports_dir=local.reports_dir)
    assert report.overall_status == "passed"
    assert report.results[0].name == "MK8s infrastructure readiness (Nebius API)"
    assert "workloads were not inspected" in report.results[0].summary
    assert {check.name for check in report.results[0].checks} == {
        "Cluster identity",
        "Cluster readiness",
        "Configured private endpoint",
        "Node-group inventory",
        "Node-group readiness: cpu",
    }
    assert all(check.status == "passed" for check in report.results[0].checks)
    destination = paths(tmp_path / "published")
    publish_deployment_reports(local, destination, [VALIDATION], report)
    published = json.loads((destination.reports_dir / "deploy-report.json").read_text())
    assert published["status"] == "passed"
    assert published["results"][0]["status"] == "passed"
    assert len(published["results"][0]["checks"]) == 5


def test_empty_node_group_inventory_still_records_executed_cluster_checks():
    payload = config()
    payload["infra"]["components"][0]["inputs"]["node_groups"] = {}
    cluster, _ = live()
    proof = snapshot(cluster, [], payload)
    assert proof["passed"]
    assert len(proof["checks"]) == 4
    assert all(check["passed"] for check in proof["checks"])


@pytest.mark.parametrize("invalid", ["missing", "empty", "skipped"])
def test_provider_acceptance_rejects_incomplete_required_report(tmp_path, monkeypatch, invalid):
    cluster, group = live()
    proof = snapshot(cluster, [group])
    if invalid == "missing":
        proof.pop("checks", None)
    elif invalid == "empty":
        proof["checks"] = []
    else:
        proof["skipped"] = True
    monkeypatch.setattr(infra, "wait_infrastructure_ready", lambda **kw: proof)
    monkeypatch.setattr(cli, "terraform_output_raw", lambda *a, **kw: "cluster-id")
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda *a: {})
    with pytest.raises(
        RuntimeError, match="Required validation was skipped or has no executed checks"
    ):
        infra.verify_infrastructure_target(
            cli, config(), paths(tmp_path), TARGET, validations=[VALIDATION]
        )


@pytest.mark.parametrize(
    "case", ["app", "gpu", "external", "integration", "validation", "binding", "baseline"]
)
def test_in_cluster_work_and_history_retain_kubernetes(case, tmp_path):
    payload, target, validations, identity = config(), dict(TARGET), [VALIDATION], None
    if case == "app":
        payload["apps"]["charts"] = [{"id": "grafana", "instance_id": "cluster", "enabled": True}]
    elif case == "gpu":
        payload["infra"]["components"][0]["inputs"]["node_groups"]["cpu"]["gpu"] = True
    elif case == "external":
        target["ownership"] = "external"
    elif case == "integration":
        cluster_rows(payload)[0]["observability"]["enabled"] = True
    elif case == "validation":
        validations = [{**VALIDATION, "kind": "mk8s_gpu_visibility"}]
    elif case == "binding":
        identity = {"cluster_id": "cluster-id", "kubernetes_uid": "uid"}
    else:
        (tmp_path / "ordinary-apps-baseline.json").write_text(
            json.dumps({"identities": {"cluster": {"kubernetes_uid": "uid"}}})
        )
    assert not infra.infrastructure_only_target(
        payload,
        target,
        validations=validations,
        application_identity=identity,
        paths=NS(reports_dir=tmp_path, project_dir=tmp_path),
    )


@pytest.mark.parametrize(
    "case",
    [
        "cluster-pending",
        "reconciling",
        "missing",
        "unready",
        "outdated",
        "count",
        "group-pending",
        "endpoint",
    ],
)
def test_provider_partial_evidence_never_passes(case):
    cluster, group = live()
    groups = [group]
    if case == "cluster-pending":
        cluster.status.state = ClusterStatus.State.PROVISIONING
    elif case == "reconciling":
        cluster.status.reconciling = True
    elif case == "missing":
        groups = []
    elif case == "unready":
        group.status.ready_node_count = 1
    elif case == "outdated":
        group.status.outdated_node_count = 1
    elif case == "count":
        group.status.target_node_count = group.status.ready_node_count = group.status.node_count = 1
    elif case == "group-pending":
        group.status.state = NodeGroupStatus.State.PROVISIONING
    else:
        cluster.status.control_plane.endpoints.private_endpoint = ""
    assert not snapshot(cluster, groups)["passed"]


@pytest.mark.parametrize(
    "case",
    [
        "cluster-id",
        "parent",
        "name",
        "extra",
        "duplicate",
        "group-parent",
        "public",
        "invalid-count",
        "deleting",
    ],
)
def test_wrong_or_ambiguous_provider_identity_is_rejected(case):
    cluster, group = live()
    groups = [group]
    if case == "cluster-id":
        cluster.metadata.id = "other"
    elif case == "parent":
        cluster.metadata.parent_id = "other"
    elif case == "name":
        cluster.metadata.name = "other"
    elif case == "extra":
        group.metadata.name = "other"
    elif case == "duplicate":
        groups.append(copy.deepcopy(group))
    elif case == "group-parent":
        group.metadata.parent_id = "other"
    elif case == "public":
        cluster.status.control_plane.endpoints.public_endpoint = "unexpected-public-test"
    elif case == "invalid-count":
        group.status.ready_node_count = None
    else:
        cluster.status.state = ClusterStatus.State.DELETING
    with pytest.raises(RuntimeError):
        snapshot(cluster, groups)


def test_autoscaling_uses_live_target_within_configured_bounds():
    payload = config()
    payload["infra"]["components"][0]["inputs"]["node_groups"]["cpu"]["autoscaling"] = {
        "enabled": True,
        "min_node_count": 1,
        "max_node_count": 3,
    }
    cluster, group = live()
    assert snapshot(cluster, [group], payload)["passed"]
    group.status.target_node_count = group.status.node_count = group.status.ready_node_count = 4
    assert not snapshot(cluster, [group], payload)["passed"]


@pytest.mark.parametrize("mode", ["ready", "converges", "pagination", "cycle", "denied", "timeout"])
def test_sdk_wait_is_bounded_complete_and_closes_client(monkeypatch, mode):
    cluster, group = live()
    closed, calls = [], []
    sdk = NS(sync_close=lambda **kw: closed.append(kw["timeout"]))
    monkeypatch.setattr(infra, "init_nebius_sdk", lambda **kw: sdk)
    ticks = [0.0]
    monkeypatch.setattr(infra.time, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(infra.time, "sleep", lambda delay: ticks.__setitem__(0, ticks[0] + delay))

    def get(request, *, timeout):
        assert request.id == "cluster-id" and 0 < timeout <= 30
        calls.append("get")
        if mode == "denied":
            raise RuntimeError("sensitive-provider-detail")
        cluster.status.state = (
            ClusterStatus.State.PROVISIONING
            if mode == "timeout" or (mode == "converges" and calls.count("get") == 1)
            else ClusterStatus.State.RUNNING
        )
        return NS(wait=lambda: cluster)

    def listed(request, *, timeout):
        assert request.parent_id == "cluster-id" and 0 < timeout <= 30
        calls.append(request.page_token)
        page = NS(items=[group], next_page_token="")
        if mode in {"pagination", "cycle"}:
            page = NS(
                items=[] if not request.page_token else [group],
                next_page_token="second" if not request.page_token or mode == "cycle" else "",
            )
        return NS(wait=lambda: page)

    monkeypatch.setattr(infra, "ClusterServiceClient", lambda sdk: NS(get=get))
    monkeypatch.setattr(infra, "NodeGroupServiceClient", lambda sdk: NS(list=listed))
    kwargs = dict(
        cluster_id="cluster-id",
        project_id="project-test",
        component=config()["infra"]["components"][0],
        target_ref="cluster",
        timeout=10,
        poll_interval=5,
    )
    if mode in {"cycle", "denied", "timeout"}:
        with pytest.raises((RuntimeError, TimeoutError)) as exc:
            infra.wait_infrastructure_ready(**kwargs)
        assert "sensitive-provider-detail" not in str(exc.value)
    else:
        proof = infra.wait_infrastructure_ready(**kwargs)
        assert proof["passed"]
        assert proof["node_groups"][0]["ready"] == 2
    if mode == "pagination":
        assert "second" in calls
    if mode == "converges":
        assert calls.count("get") == 2
    assert closed == [5]


def test_final_infrastructure_verification_reobserves_without_app_journal_binding(
    tmp_path, monkeypatch
):
    from nebius_cxcli import deployment_applications
    from nebius_cxcli.deployment_cli import _CliDeploymentExecutor

    local = paths(tmp_path)
    cluster, group = live()
    calls = []
    monkeypatch.setattr(
        infra,
        "wait_infrastructure_ready",
        lambda **kw: calls.append(kw) or snapshot(cluster, [group]),
    )
    monkeypatch.setattr(cli, "terraform_output_raw", lambda *a, **kw: "cluster-id")
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda *a: {})
    monkeypatch.setattr(deployment_applications, "target_bundle_digest", lambda *a: "bundle")
    executor = object.__new__(_CliDeploymentExecutor)
    executor.cli, executor.config, executor.paths, executor.report_paths = (
        cli,
        config(),
        local,
        local,
    )
    executor.manifest = {"execution": {"validations": [VALIDATION]}}
    executor.accepted_evidence = {
        "targets": {"cluster": {"infrastructure": {"cluster_id": "cluster-id"}}}
    }
    executor._verified_targets = {}
    executor.lease = NS(assert_held=lambda: None)
    executor._bound_identities = lambda: {}
    executor._selected_target = lambda ref: TARGET
    executor._desired_application_generation = lambda: None
    executor._application_journal = lambda: pytest.fail(
        "Cloud evidence must not use app identity journal"
    )
    first = executor._verify_application("cluster")
    assert first["infrastructure"]["passed"] and "identity" not in first
    assert executor._verify_application("cluster") == first
    assert len(calls) == 1
    executor._verify_application("cluster", fresh=True)
    assert len(calls) == 2
    monkeypatch.setattr(cli, "terraform_output_raw", lambda *a, **kw: "replacement")
    with pytest.raises(RuntimeError, match="accepted Terraform identity"):
        executor._verify_application("cluster", fresh=True)


def test_private_managed_baseline_does_not_require_kubernetes(tmp_path, monkeypatch):
    import yaml

    from nebius_cxcli import ordinary_apps
    from nebius_cxcli.deployment_state import DeploymentGeneration
    from nebius_cxcli.generated_manifest import (
        build_generated_manifest,
        manifest_path_for_generated_dir,
    )

    local = paths(tmp_path)
    payload = config()
    local.config_path.write_text(yaml.safe_dump(payload))
    local.infra_dir.mkdir(parents=True)
    (local.infra_dir / "main.tf").write_text("# managed cluster fixture\n")
    target = {
        **TARGET,
        "component_output_ref": "mk8s.cluster",
        "access": "internal",
        "flux_dir": str(flux_target_dir(local, "cluster")),
    }
    manifest = build_generated_manifest(
        config=payload, paths=local, targets=[target], required_component_outputs=[]
    )
    manifest["execution"]["validations"] = [VALIDATION]
    manifest_path_for_generated_dir(local.generated_dir).write_text(json.dumps(manifest))
    generation = DeploymentGeneration.capture(local, manifest)

    def forbidden(*a, **kw):
        pytest.fail("Infrastructure baseline attempted Kubernetes access")

    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", forbidden)
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", forbidden)
    assert cli.accept_ordinary_app_baseline(
        local,
        identities={},
        selected_target_refs=("cluster",),
        deployment_generation=generation.identity,
        expected_generation=generation,
    )
    baseline = json.loads((local.reports_dir / ordinary_apps.BASELINE_FILENAME).read_text())
    assert baseline["identities"] == {}
    assert baseline["deployment_generation"] == generation.identity


def test_failed_provider_validation_writes_failed_report(tmp_path, monkeypatch):
    local = paths(tmp_path)
    monkeypatch.setattr(cli, "terraform_output_raw", lambda *a, **kw: "cluster-id")
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda *a: {})

    def denied(**kwargs):
        raise RuntimeError("denied")

    monkeypatch.setattr(infra, "wait_infrastructure_ready", denied)
    with pytest.raises(RuntimeError, match="denied"):
        infra.verify_infrastructure_target(cli, config(), local, TARGET, validations=[VALIDATION])
    report = build_deploy_validation_report([VALIDATION], reports_dir=local.reports_dir)
    assert report.overall_status == "failed"
    assert report.results[0].name == "MK8s infrastructure readiness (Nebius API)"


@pytest.mark.parametrize("history", ["application", "infrastructure", "other-target", "malformed"])
def test_completed_local_history_disqualifies_application_targets_without_baseline(
    tmp_path, history
):
    from nebius_cxcli.deployment_local import LocalObjectStore
    from nebius_cxcli.deployment_state import SCHEMA, DeploymentState
    from nebius_cxcli.terraform_backend import backend_settings_from_config

    local = paths(tmp_path)
    payload = config()
    store = LocalObjectStore.for_project(local)
    state = DeploymentState(store, backend_settings_from_config(payload), assert_held=lambda: None)
    ref = "other" if history == "other-target" else "cluster"
    evidence = {
        "identities": {ref: {"cluster_id": "id", "kubernetes_uid": "uid"}},
        "targets": {ref: {"identity": {"cluster_id": "id", "kubernetes_uid": "uid"}}},
    }
    if history == "infrastructure":
        evidence = {"identities": {}, "targets": {ref: {"infrastructure": {"cluster_id": "id"}}}}
    record = {
        "schema": SCHEMA,
        "active": None,
        "accepted": {"generation": "sha256:" + "a" * 64, "evidence": evidence},
    }
    if history == "malformed":
        record["accepted"]["evidence"] = []
    store.write(state.prefix + "/state.json", record, etag=None)
    assert not (local.reports_dir / "ordinary-apps-baseline.json").exists()
    if history == "malformed":
        with pytest.raises(RuntimeError, match="Incomplete deployment"):
            infra.infrastructure_only_target(payload, TARGET, validations=[VALIDATION], paths=local)
    else:
        assert infra.infrastructure_only_target(
            payload, TARGET, validations=[VALIDATION], paths=local
        ) == (history in {"infrastructure", "other-target"})


@pytest.mark.parametrize("final_failure", [False, True])
def test_mixed_dispatch_and_final_acceptance_keep_provider_and_application_evidence_separate(
    tmp_path,
    monkeypatch,
    final_failure,
):
    from nebius_cxcli import deployment_applications
    from nebius_cxcli.deployment_cli import _CliDeploymentExecutor
    from nebius_cxcli.deployment_plan import DeploymentAction, DeploymentPlan
    from nebius_cxcli.deployment_state import DeploymentGeneration, DeploymentState
    from test_deployment_state import Store, settings

    local, payload = paths(tmp_path), config()
    payload["apps"]["charts"] = [{"id": "grafana", "instance_id": "app", "enabled": True}]
    targets = [
        {**TARGET, "flux_dir": str(flux_target_dir(local, "cluster"))},
        {
            "target_ref": "app",
            "instance_id": "app",
            "ownership": "external",
            "kind": "external-mk8s",
            "kube_context": "app-context",
            "access": "external",
            "flux_dir": str(flux_target_dir(local, "app")),
        },
    ]
    manifest = {
        "runtime_config": payload,
        "execution": {
            "targets": targets,
            "validations": [VALIDATION],
            "backend": {},
            "required_component_outputs": [],
            "status_watchers": [],
        },
        "schema": "nebius-cxcli-generated/v3",
    }
    cluster, group = live()
    reads = []

    def provider(**kwargs):
        reads.append(kwargs)
        if final_failure and len(reads) > 1:
            raise RuntimeError("fresh provider evidence unavailable")
        return snapshot(cluster, [group])

    monkeypatch.setattr(infra, "wait_infrastructure_ready", provider)
    monkeypatch.setattr(cli, "terraform_output_raw", lambda *a, **kw: "cluster-id")
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda *a: {})
    monkeypatch.setattr(cli, "_enabled_status_watcher_specs", lambda *a: [])
    monkeypatch.setattr(cli, "_manifest_deploy_targets", lambda *a: targets)
    monkeypatch.setattr(cli, "_resolve_deploy_run_targets", lambda *a, **kw: targets)
    monkeypatch.setattr(
        cli, "write_inventory", lambda *a, **kw: NS(markdown=local.reports_dir / "deploy-report.md")
    )
    app_identity = {"cluster_id": "app-cluster-id", "kubernetes_uid": "real-app-uid"}

    def dispatch(services, conf, current_paths, target, **kwargs):
        if target["target_ref"] == "app":
            # Existing application reconciliation has its own end-to-end tests.
            return {"identity": app_identity, "grafana_statuses": [], "bootstrap_commands": []}
        return deploy_application_target(services, conf, current_paths, target, **kwargs)

    monkeypatch.setattr(cli, "deploy_application_target", dispatch)
    lease = NS(assert_held=lambda: None)
    summary = cli._deploy_generated_artifacts(
        payload,
        local,
        manifest,
        skip_validations=False,
        skip_validation_kinds=set(),
        skip_terraform_apply=True,
        preflight_runtime_env={},
        deployment_lease=lease,
        prepare_application_inputs=lambda: None,
    )
    assert summary.cluster_identities == {"app": app_identity}
    assert len(reads) == 1
    frozen = DeploymentGeneration(manifest, {})
    executor = object.__new__(_CliDeploymentExecutor)
    executor.cli, executor.config, executor.paths, executor.report_paths = (
        cli,
        payload,
        local,
        local,
    )
    executor.manifest, executor.generation = manifest, frozen
    executor.accepted_evidence, executor._verified_targets = {}, {}
    executor.identities, executor.summary, executor.lease = (
        summary.cluster_identities,
        summary,
        lease,
    )
    executor.owned_settings, executor.compatibility_report = {}, {}
    executor._selected_target = lambda ref: next(row for row in targets if row["target_ref"] == ref)
    executor._bound_identities = lambda: summary.cluster_identities
    executor._desired_application_generation = lambda **kw: frozen
    executor.observe = lambda previous: (None, False, {})
    executor._terraform_plan = lambda **kw: (None, {"resource_changes": []})
    monkeypatch.setattr(deployment_applications, "target_bundle_digest", lambda *a: "bundle")
    cloud_verify = executor._verify_application
    executor._verify_application = lambda ref, **kw: (
        {"identity": app_identity, "desiredBundle": "app-bundle", "validations": "passed"}
        if ref == "app"
        else cloud_verify(ref, **kw)
    )
    plan = DeploymentPlan(DeploymentAction.RECONCILE, "", "", "", (), (), ("cluster", "app"))
    state = DeploymentState(Store(), settings(), assert_held=lambda: None)
    record = state.begin(frozen, plan={"semanticPlan": plan.as_payload()})
    if final_failure:
        with pytest.raises(RuntimeError, match="fresh provider evidence"):
            executor.verify_final(plan)
        assert state.read().value["accepted"] is None
    else:
        proof = executor.verify_final(plan)
        completed = state.accept(record, evidence=proof).value["accepted"]["evidence"]
        assert completed["identities"] == {"app": app_identity}
        assert "identity" not in completed["targets"]["cluster"]
        assert completed["targets"]["cluster"]["infrastructure"]["passed"]
        assert completed["targets"]["app"]["identity"] == app_identity
    assert len(reads) == 2
