import base64
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import deployment_dashboard_repair as dashboard
from nebius_cxcli import deployment_install_repair as bridge
from nebius_cxcli.deployment_applications import ApplicationJournal, target_bundle_digest
from nebius_cxcli.deployment_bundle_files import target_files
from nebius_cxcli.deployment_cli import _CliDeploymentExecutor
from nebius_cxcli.deployment_state import DeploymentGeneration, digest
from nebius_cxcli.soperator_adapter import render_soperator_monitoring_dashboard_documents
from nebius_cxcli.soperator_install_render_repair import (
    DASHBOARD_FILE,
    GRAPH_FILE,
    OUTER_FILE,
    REPAIR_REASON,
    _file_hashes,
    dashboard_repair_candidate,
)
from nebius_cxcli.soperator_receipt_io import write_owner_only_json
from test_soperator_install_render_repair import DIGEST, render_delta  # noqa: F401
from test_soperator_upstream_adapter import _release_with_monitoring_chart, _write_dashboard_source

REVIEWED_RELEASES = (
    ("4.1.9", "sha256:3bd979e84f4c7e5356cc6b6546f51e0719f0b531c9f1eb1376851a8d1c34b93f"),
    ("4.1.11", "sha256:71a2c115845e403e8c6a24ade3a4d29c5aa2cbfc5a1cdd1728af91b1ca1dc1cd"),
)


@pytest.fixture(params=["standard", "fast-dev-test"])
def deployment_profile(request):
    return request.param


@pytest.fixture(params=REVIEWED_RELEASES, ids=lambda item: item[0])
def bundles(tmp_path, monkeypatch, request, deployment_profile):
    release_name, chart_digest = request.param
    before, _, _ = request.getfixturevalue("render_delta")
    before = {
        name: raw.replace(b"4.1.5", release_name.encode()).replace(
            DIGEST.encode(), chart_digest.encode()
        )
        for name, raw in before.items()
    }
    before["ordinary/app.yaml"] = b"apiVersion: v1\nkind: Namespace\nmetadata: {name: ordinary}\n"
    before["soperator-nebius-adapter.yaml"] = (
        b"apiVersion: v1\nkind: ConfigMap\nmetadata: {name: adapter}\ndata: {state: retained}\n"
    )
    outer = yaml.safe_load(before[OUTER_FILE])
    if deployment_profile == "fast-dev-test":
        from nebius_cxcli.soperator_deployment_profile import _coverage
        from nebius_cxcli.soperator_install_render_repair import VALUES_FILE

        values_cm = yaml.safe_load(before[VALUES_FILE])
        values = yaml.safe_load(values_cm["data"]["values.yaml"])
        values["cxcliDiagnostics"] = _coverage()
        values_cm["data"]["values.yaml"] = yaml.safe_dump(values, sort_keys=False)
        before[VALUES_FILE] = yaml.safe_dump(values_cm, sort_keys=False).encode()
        outer["spec"]["values"]["cxcliDiagnostics"] = _coverage()
    outer.update(
        apiVersion="helm.toolkit.fluxcd.io/v2",
        metadata={"name": "outer", "namespace": "flux-system"},
    )
    for patch in outer["spec"]["postRenderers"][0]["kustomize"]["patches"]:
        if patch["target"]["name"] == "cxcli-soperator-fluxcd-monitoring-dashboards":
            continue
        is_main = patch["target"]["name"] == "keep"
        patch["target"] = {
            "group": "helm.toolkit.fluxcd.io",
            "version": "v2",
            "kind": "HelmRelease",
            "name": "soperator-fluxcd-slurm-cluster"
            if is_main
            else "soperator-fluxcd-monitoring-dashboards",
        }
        patch["patch"] = yaml.safe_dump(
            [
                {
                    "op": "replace",
                    "path": "/metadata/name",
                    "value": "main" if is_main else "cxcli-soperator-fluxcd-monitoring-dashboards",
                }
            ]
        )
    before[OUTER_FILE] = yaml.safe_dump(outer, sort_keys=False).encode()
    graph = list(yaml.safe_load_all(before[GRAPH_FILE]))
    for doc in graph:
        doc["apiVersion"] = (
            "source.toolkit.fluxcd.io/v1" if doc["kind"] == "OCIRepository" else "v1"
        )
        if doc["kind"] == "ConfigMap":
            contract = json.loads(doc["data"]["graph.json"])
            for row in contract["releases"]:
                row["namespace"] = "flux-system"
                row["sourceKind"] = "OCIRepository"
                row.setdefault("sourceName", "main-source")
                row["upstreamReleaseName"] = (
                    "soperator-fluxcd-slurm-cluster"
                    if row["isMain"]
                    else "soperator-fluxcd-monitoring-dashboards"
                )
            doc["data"]["graph.json"] = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    before[GRAPH_FILE] = yaml.safe_dump_all(graph, sort_keys=False).encode()
    before["kustomization.yaml"] = yaml.safe_dump({"resources": list(before)}).encode()
    release = _release_with_monitoring_chart(chart_digest)
    release = replace(
        release,
        release=release_name,
        charts={k: replace(v, version=release_name) for k, v in release.charts.items()},
    )
    _write_dashboard_source(tmp_path)
    monkeypatch.setattr(dashboard, "load_soperator_release_snapshot", lambda _: release)
    monkeypatch.setattr(
        dashboard,
        "ensure_soperator_release_source",
        lambda _: SimpleNamespace(source_dir=str(tmp_path)),
    )
    docs = render_soperator_monitoring_dashboard_documents(
        {"observability": {"enabled": True}}, release=release, source_root=tmp_path
    )
    after = dashboard_repair_candidate(
        before, release=release_name, chart_digest=chart_digest, dashboards=docs
    )

    def generation(files):
        return DeploymentGeneration(
            {"deploy": {"targets": [{"target_ref": "cluster", "flux_dir": "flux/cluster"}]}},
            {"flux/cluster/" + name: base64.b64encode(raw).decode() for name, raw in files.items()},
        )

    old, new = generation(before), generation(after)
    journal = ApplicationJournal(
        tmp_path / "journal.json", generation=digest("generation"), selected=["cluster"]
    )
    identity = {"cluster_id": "cluster-id", "kubernetes_uid": "cluster-uid"}
    journal.bind("cluster", identity=identity, desired=target_bundle_digest(old, "cluster"))
    paths = SimpleNamespace(reports_dir=tmp_path, flux_dir=tmp_path / "flux")
    executor = SimpleNamespace(
        paths=paths,
        manifest=old.manifest,
        _selected_target=lambda _: {},
        cli=SimpleNamespace(_paths_for_target_flux_dir=lambda *a: paths),
        _application_journal=lambda: journal,
        lease=SimpleNamespace(assert_held=lambda: None),
        _desired_application_generation=lambda **kw: new,
    )
    return executor, old, new, journal, identity


@pytest.mark.parametrize("committed", [False, True])
def test_shared_deploy_preserves_predecessor_until_cluster_admission(
    bundles, monkeypatch, committed
):
    executor, old, new, journal, _ = bundles
    current = new if committed else old
    if committed:
        before, after = target_files(old, "cluster"), target_files(new, "cluster")
        write_owner_only_json(
            executor.paths.reports_dir / "soperator-install-render-repair-cluster.json",
            {
                "schema": REPAIR_REASON,
                "targetRef": "cluster",
                "previousFiles": _file_hashes(before),
                "replacementFiles": _file_hashes(after),
                "predecessorFiles": {k: base64.b64encode(v).decode() for k, v in before.items()},
            },
        )
    monkeypatch.setattr(DeploymentGeneration, "capture", lambda *a: current)
    _CliDeploymentExecutor._preflight_install_application_inputs(executor)
    _CliDeploymentExecutor._prepare_install_application_inputs(executor)
    transition = executor._install_input_transitions["cluster"]
    assert transition.reason == REPAIR_REASON
    assert transition.previous_bundle == target_bundle_digest(old, "cluster")
    assert transition.desired_bundle == target_bundle_digest(new, "cluster")
    assert journal.entry("cluster")["desiredBundle"] == transition.previous_bundle


@pytest.mark.parametrize(
    "change",
    ["prior", "complete", "already_repaired", "dashboard", "ordinary", "digest", "receipt"],
)
def test_dashboard_bridge_rejects_unapproved_changes(bundles, monkeypatch, change):
    executor, old, new, journal, _ = bundles
    entry = dict(journal.entry("cluster"))
    if change == "prior":
        entry["desiredBundle"] = digest("wrong")
    elif change == "complete":
        entry["status"] = "complete"
    elif change == "already_repaired":
        entry["inputRepair"] = {"already": True}
    elif change == "digest":
        monkeypatch.setattr(
            dashboard, "SOPERATOR_MONITORING_DASHBOARDS_POST_FLUX_DIGESTS", frozenset()
        )
    elif change == "receipt":
        write_owner_only_json(
            executor.paths.reports_dir / "soperator-install-render-repair-cluster.json",
            {"schema": REPAIR_REASON, "targetRef": "foreign"},
        )
    else:
        files = dict(new.files)
        name = DASHBOARD_FILE if change == "dashboard" else "ordinary/app.yaml"
        files["flux/cluster/" + name] = base64.b64encode(
            base64.b64decode(files["flux/cluster/" + name]) + b"# unapproved\n"
        ).decode()
        if change == "dashboard":
            files["flux/cluster/" + name] = base64.b64encode(
                base64.b64decode(files["flux/cluster/" + name]).replace(
                    b"grafana_dashboard: '1'", b"grafana_dashboard: '0'"
                )
            ).decode()
        new = replace(new, files=files)
    with pytest.raises(RuntimeError):
        bridge.install_input_transition(
            executor, current=old, desired=new, target_ref="cluster", entry=entry
        )


@pytest.mark.parametrize("accepted", [False, True])
def test_shared_admission_uses_dashboard_owner_and_only_publishes_sealed_delta(
    bundles, monkeypatch, accepted
):
    executor, old, new, journal, identity = bundles
    transition = bridge.install_input_transition(
        executor, current=old, desired=new, target_ref="cluster", entry=journal.entry("cluster")
    )
    executor._install_input_transitions = {"cluster": transition}
    executor.cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV = "context"
    executor.cli._read_soperator_slurm_cluster_journal = lambda **kw: ({}, "version")
    write_owner_only_json(executor.paths.reports_dir / "soperator-slurm-actions-cluster.json", {})
    monkeypatch.setattr(bridge, "_files", lambda _: target_files(old, "cluster"))
    receipt = {
        "schema": REPAIR_REASON,
        "previousFiles": transition.previous_files,
        "replacementFiles": transition.replacement_files,
        "predecessorReceipt": {
            "operation": {
                "spec": {
                    "nebius_cluster_id": identity["cluster_id"],
                    "kubernetes_uid": identity["kubernetes_uid"],
                }
            }
        },
    }

    def prepare(**kwargs):
        assert "slurm" not in kwargs
        return receipt if accepted else None

    monkeypatch.setattr(bridge, "prepare_install_dashboard_repair", prepare)

    def call():
        bridge.admit_storage_input_transition(
            executor, "cluster", identity, {"context": "ctx"}, lambda: None
        )

    if accepted:
        call()
        restored = ApplicationJournal(
            journal.path, generation=journal.payload["generation"], selected=["cluster"]
        )
        assert restored.entry("cluster")["inputRepair"]["reason"] == REPAIR_REASON
        assert restored.entry("cluster")["desiredBundle"] == transition.desired_bundle
        assert not executor._install_input_transitions
    else:
        with pytest.raises(RuntimeError, match="cluster-sealed"):
            call()
        assert journal.entry("cluster")["desiredBundle"] == transition.previous_bundle


@pytest.mark.parametrize("failed_apply", [False, True])
def test_native_owner_receipt_replays_after_shared_journal_interruption(
    bundles, monkeypatch, failed_apply, deployment_profile
):
    from nebius_cxcli import soperator_install_render_repair as owner

    executor, old, new, journal, identity = bundles
    paths = executor.paths
    paths.project_dir = paths.reports_dir
    before = target_files(old, "cluster")
    for name, content in before.items():
        path = paths.flux_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    snapshot = dashboard.load_soperator_release_snapshot(None)
    monkeypatch.setattr(owner, "load_soperator_release_snapshot", lambda _: snapshot)
    monkeypatch.setattr(
        owner,
        "frozen_soperator_release_from_snapshot",
        lambda _: SimpleNamespace(source=SimpleNamespace(source_dir=str(paths.project_dir))),
    )
    monkeypatch.setattr(owner, "soperator_stage_plan_sha256", lambda _: digest("graph"))
    stage_digest = owner.soperator_reconcile_stage_plan_sha256(
        strategy="install",
        rendered_graph_sha256=digest("graph"),
        deployment_profile=deployment_profile,
    )
    spec = {
        "strategy": "install",
        "current_release": "",
        "target_release": snapshot.release,
        "target_ref": "cluster",
        "intervention_generation": 0,
        "desired_values_sha256": _file_hashes(before)[owner.VALUES_FILE],
        "adapter_sha256": _file_hashes(before)["soperator-nebius-adapter.yaml"],
        "stage_plan_sha256": stage_digest,
        "nebius_cluster_id": identity["cluster_id"],
        "kubernetes_uid": identity["kubernetes_uid"],
    }
    scheduling = {"operationSpecSha256": digest(spec), "lastCompletedStage": "gated", "actions": []}
    write_owner_only_json(paths.reports_dir / "soperator-slurm-actions-cluster.json", scheduling)
    apply_transition = {
        "id": "apply-transition",
        "phase": "apply-declarative-release",
        "status": "failed" if failed_apply else "running",
    }
    if failed_apply:
        apply_transition.update(
            failureType="operation-error", failureAttempts=1, receiptSha256=None
        )
    write_owner_only_json(
        paths.reports_dir / "soperator-release-reconcile-test.json",
        {
            "status": "recovery-required" if failed_apply else "running",
            "operation": {"spec": spec},
            "irreversibleFrontier": None,
            "irreversibleIntent": {
                "transitionId": "apply-transition",
                "phase": "apply-declarative-release",
                "disposition": "pending-forward-only",
            },
            "transitions": [
                {"phase": "resolve-immutable-sources", "status": "complete"},
                {"phase": "establish-boot-storage-barrier", "status": "complete"},
                apply_transition,
            ],
        },
    )

    def kube_get(args, **kwargs):
        if args[0] == "crd":
            return None
        is_main = args[1] == "main"
        return {
            "metadata": {
                "name": args[1],
                "namespace": "flux-system",
                "uid": args[1] + "-uid",
                "generation": 2,
                "labels": {
                    "soperator.nebius.ai/release-graph": "nebius-cxcli",
                    "app.kubernetes.io/version": snapshot.release,
                },
            },
            "spec": {
                "chartRef": {
                    "kind": "OCIRepository",
                    "name": "main-source" if is_main else owner.DASHBOARD_SOURCE,
                },
                "suspend": is_main,
            },
            "status": {}
            if is_main
            else {
                "observedGeneration": 1,
                "lastAttemptedRevisionDigest": snapshot.charts["monitoringDashboards"].digest,
                "conditions": [
                    {
                        "type": "Ready",
                        "status": "False",
                        "reason": "InstallFailed",
                        "observedGeneration": 2,
                        "message": "invalid document separator: ---apiVersion: v1",
                    }
                ],
            },
        }

    monkeypatch.setattr(owner, "_kube_get", kube_get)
    sealed = []

    def seal(receipt, **kwargs):
        if kwargs["create"]:
            sealed.append(digest(receipt))
        else:
            assert digest(receipt) == sealed[0]

    monkeypatch.setattr(owner, "_bind_repair_admission", seal)
    # The native owner publishes its transaction; the shared journal remains old.
    receipt = owner.prepare_install_dashboard_repair(
        paths=paths,
        target_ref="cluster",
        scheduling_journal=scheduling,
        local_scheduling_journal=scheduling,
        env={},
        kube_context="ctx",
        assert_authority=lambda: None,
    )
    assert {k: base64.b64decode(v) for k, v in receipt["predecessorFiles"].items()} == before
    assert journal.entry("cluster")["desiredBundle"] == target_bundle_digest(old, "cluster")
    current = replace(
        new,
        files={
            "flux/cluster/" + k: base64.b64encode(v).decode()
            for k, v in owner._files(paths.flux_dir).items()
        },
    )
    transition = bridge.install_input_transition(
        executor, current=current, desired=new, target_ref="cluster", entry=journal.entry("cluster")
    )
    executor._install_input_transitions = {"cluster": transition}
    executor.cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV = "context"
    executor.cli._read_soperator_slurm_cluster_journal = lambda **kw: (scheduling, "version")
    bridge.admit_storage_input_transition(
        executor, "cluster", identity, {"context": "ctx"}, lambda: None
    )
    assert journal.entry("cluster")["desiredBundle"] == transition.desired_bundle
    assert journal.entry("cluster")["inputRepair"]["admissionSha256"] == sealed[0]
