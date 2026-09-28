import base64
import copy
import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import soperator_observability_transition as transition
from nebius_cxcli.deployment_applications import target_bundle_digest
from nebius_cxcli.deployment_bundle_files import target_files
from nebius_cxcli.deployment_state import DeploymentGeneration, DeploymentState, digest
from nebius_cxcli.flux_render import _materialize_soperator_observability_values, _multi_doc_yaml
from nebius_cxcli.soperator_flux_graph import (
    render_soperator_flux_graph_documents,
    soperator_graph_post_render_patches,
)
from nebius_cxcli.soperator_install_render_repair import GRAPH_FILE, OUTER_FILE, VALUES_FILE
from nebius_cxcli.soperator_release_reconciler import (
    SoperatorReconcileRepairLineage,
    reconcile_soperator_release,
    resolve_soperator_reconcile_strategy,
)
from test_deployment_state import Store, settings
from test_soperator_flux_graph import _core_values, _snapshot
from test_soperator_release_reconciler import _artifacts, _callbacks, _paths, _source, _spec


def encoded(value):
    return base64.b64encode(json.dumps(value, sort_keys=True).encode()).decode()


def authored():
    values = {
        "clusterName": "slurm",
        "soperator-dcgm-exporter": {
            "enabled": False,
            "validateToolkit": False,
            "fullnameOverride": "soperator-dcgm-exporter",
            "serviceMonitor": {"enabled": False},
        },
    }
    gpu = {"driver": {"enabled": False}}
    return {
        "client_info": {
            "nebius": {"tenant_id": "tenant", "project_id": "project", "region_id": "region"}
        },
        "infra": {"components": []},
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "enabled": True,
                    "target_ref": "cluster-a",
                    "instance_id": "cluster-a",
                    "values": values,
                },
                {
                    "id": "nvidia-gpu-operator",
                    "enabled": True,
                    "target_ref": "cluster-a",
                    "instance_id": "cluster-a",
                    "values": gpu,
                },
            ],
            "slurm": {
                "soperator_cluster_a": {
                    **copy.deepcopy(values),
                    "target_ref": "cluster-a",
                    "version": "4.1.8",
                }
            },
            "platform": {
                "gpu_cluster_a": {**copy.deepcopy(gpu), "target_ref": "cluster-a", "version": "1.0"}
            },
        },
    }


@pytest.fixture
def bundles(tmp_path, monkeypatch):
    from nebius_cxcli import soperator_release_graph
    from soperator_fixtures import sample_selected_graph

    monkeypatch.setattr(
        soperator_release_graph,
        "render_soperator_release_graph",
        lambda lock, _source, values: sample_selected_graph(lock, values),
    )
    old_config = authored()
    new_config = transition.corrected_config(old_config)
    before = _core_values()
    before["observability"] = {
        "enabled": True,
        "dcgmExporter": {"enabled": False},
        "vmStack": {"enabled": False},
    }
    defaults = {
        "enabled": True,
        "dcgmExporter": {"enabled": True, "values": {"validateToolkit": True}},
        "vmStack": {"enabled": False},
    }
    telemetry = {name: {"enabled": False} for name in ("logs", "events", "metrics")}
    telemetry["enabled"] = False
    before["observability"]["opentelemetry"] = copy.deepcopy(telemetry)
    defaults["opentelemetry"] = copy.deepcopy(telemetry)
    after = copy.deepcopy(before)
    after["observability"] = copy.deepcopy(defaults)
    after["observability"]["dcgmExporter"]["values"]["validateToolkit"] = False
    binding = {"clusterName": "slurm", "observability": after["observability"]}
    _materialize_soperator_observability_values(
        payload=new_config,
        values=binding,
        target_ref="cluster-a",
        release="4.1.7",
        resolved_component_outputs={},
    )
    snapshot = _snapshot(after)
    source = SimpleNamespace(source_dir=str(tmp_path))
    frozen = SimpleNamespace(
        snapshot=snapshot,
        source=source,
        source_context=SimpleNamespace(source=source, umbrella=snapshot.umbrella),
    )
    source = tmp_path / snapshot.umbrella.source_path / "values.yaml"
    source.parent.mkdir(parents=True)
    source.write_text(yaml.safe_dump({"observability": defaults}))

    def files(values, native):
        cm = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "values"},
            "data": {"values.yaml": yaml.safe_dump(values)},
        }
        hr = {
            "apiVersion": "helm.toolkit.fluxcd.io/v2",
            "kind": "HelmRelease",
            "metadata": {"name": "umbrella", "namespace": "flux-system"},
            "spec": {
                "values": values,
                "chartRef": {"kind": "OCIRepository", "name": "frozen"},
                "postRenderers": [
                    {
                        "kustomize": {
                            "patches": soperator_graph_post_render_patches(
                                snapshot,
                                values,
                                release_graph=sample_selected_graph(snapshot, values),
                                adapter_documents=[],
                            )
                        }
                    }
                ],
            },
        }
        gpu_values = {"driver": {"enabled": False}}
        if native:
            gpu_values["dcgmExporter"] = {"enabled": False}
        gpu = {
            "apiVersion": "helm.toolkit.fluxcd.io/v2",
            "kind": "HelmRelease",
            "metadata": {"name": "gpu"},
            "spec": {"values": gpu_values, "chartRef": {"kind": "OCIRepository", "name": "gpu"}},
        }
        return {
            VALUES_FILE: _multi_doc_yaml([cm]).encode(),
            OUTER_FILE: _multi_doc_yaml([hr]).encode(),
            GRAPH_FILE: _multi_doc_yaml(
                render_soperator_flux_graph_documents(
                    snapshot,
                    values,
                    release_graph=sample_selected_graph(snapshot, values),
                    adapter_documents=[],
                )
            ).encode(),
            "soperator-nebius-adapter.yaml": b"apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: unchanged\n",
            "ordinary/helmrelease-platform-gpu-operator.yaml": _multi_doc_yaml([gpu]).encode(),
            "kustomization.yaml": yaml.safe_dump(
                {"resources": [VALUES_FILE, OUTER_FILE, GRAPH_FILE]}
            ).encode(),
        }

    def generation(config, content):
        return DeploymentGeneration(
            {
                "runtime_config": config,
                "render": {"application_inputs": {}, "compatibility": {}},
                "deploy": {"targets": [{"target_ref": "cluster-a", "flux_dir": "flux/cluster-a"}]},
            },
            {
                **{"flux/cluster-a/" + k: base64.b64encode(v).decode() for k, v in content.items()},
                "infra/main.tf": base64.b64encode(b"unchanged").decode(),
            },
        )

    return (
        generation(old_config, files(before, False)),
        generation(new_config, files(after, True)),
        frozen,
    )


def test_closed_source_correction_preserves_alias_metadata_and_unrelated_values():
    before = authored()
    result = transition.corrected_config(before)
    assert before == authored()
    assert result["apps"]["charts"][0]["values"]["observability"] == transition.NATIVE_VALUES
    assert result["apps"]["slurm"]["soperator_cluster_a"]["version"] == "4.1.8"
    assert result["apps"]["platform"]["gpu_cluster_a"]["dcgmExporter"] == {"enabled": False}


@pytest.mark.parametrize(
    "change", ["explicit", "enabled", "monitor", "custom", "name", "native", "gpu"]
)
def test_source_guard_rejects_custom_policy(change):
    config = authored()
    row = config["apps"]["charts"][0]
    old = row["values"]["soperator-dcgm-exporter"]
    if change == "explicit":
        row["values-explicit-paths"] = ["/soperator-dcgm-exporter/enabled"]
    if change == "enabled":
        old["enabled"] = True
    if change == "monitor":
        old["serviceMonitor"]["enabled"] = True
    if change == "custom":
        old["image"] = "custom"
    if change == "name":
        old["fullnameOverride"] = "custom-exporter"
    if change == "native":
        row["values"]["observability"] = {}
    if change == "gpu":
        config["apps"]["charts"][1]["values"]["dcgmExporter"] = {"enabled": True}
    with pytest.raises(RuntimeError):
        transition.corrected_config(config)


def validate(bundles, candidate=None):
    old, new, frozen = bundles
    transition.validate_render_transition(
        target_files(old, "cluster-a"),
        candidate or target_files(new, "cluster-a"),
        frozen=frozen,
        config=new.manifest["runtime_config"],
        target_ref="cluster-a",
        render_inputs={},
    )


def test_render_guard_derives_native_defaults_and_both_exporters(bundles):
    validate(bundles)


@pytest.mark.parametrize(
    "change",
    [
        "cluster",
        "label",
        "values",
        "wrapper",
        "patch",
        "graph",
        "source",
        "gpu",
        "file",
        "inventory",
    ],
)
def test_render_guard_rejects_unrelated_or_unbound_deltas(bundles, change):
    files = target_files(bundles[1], "cluster-a")
    if change in {"cluster", "label", "values", "wrapper"}:
        doc = yaml.safe_load(files[VALUES_FILE])
        values = yaml.safe_load(doc["data"]["values.yaml"])
        if change == "cluster":
            values["observability"]["clusterId"] = "foreign"
        if change == "label":
            values["observability"]["vmStack"]["values"]["vmagent"]["spec"]["externalLabels"][
                "iam_project_id"
            ] = "foreign"
        if change == "values":
            values["nodesets"]["enabled"] = False
        if change == "wrapper":
            doc["metadata"]["name"] = "foreign"
        doc["data"]["values.yaml"] = yaml.safe_dump(values)
        files[VALUES_FILE] = yaml.safe_dump(doc).encode()
    if change == "patch":
        doc = yaml.safe_load(files[OUTER_FILE])
        doc["spec"]["postRenderers"][0]["kustomize"]["patches"][0]["patch"] = "[]"
        files[OUTER_FILE] = yaml.safe_dump(doc).encode()
    if change in {"graph", "source"}:
        docs = list(yaml.safe_load_all(files[GRAPH_FILE]))
        if change == "source":
            docs[0]["spec"]["ref"] = {"digest": "sha256:" + "0" * 64}
        else:
            cm = next(d for d in docs if d["kind"] == "ConfigMap")
            graph = json.loads(cm["data"]["graph.json"])
            graph["releases"][0]["dependencies"] = ["foreign"]
            cm["data"]["graph.json"] = json.dumps(graph)
        files[GRAPH_FILE] = _multi_doc_yaml(docs).encode()
    if change == "gpu":
        files["ordinary/helmrelease-platform-gpu-operator.yaml"] = b"kind: Secret\n"
    if change == "file":
        files["soperator-nebius-adapter.yaml"] = b"kind: ConfigMap\n"
    if change == "inventory":
        files["extra.yaml"] = b"{}"
    with pytest.raises((RuntimeError, ValueError)):
        validate(bundles, files)


@pytest.fixture
def interrupted(tmp_path, bundles):
    old, new, frozen = bundles
    paths = _paths(tmp_path / "operation")
    strategy = resolve_soperator_reconcile_strategy(
        current_release=None,
        target_release=frozen.snapshot.release,
        source_contract=None,
        target_contract=frozen.snapshot.capability_contract,
    )
    spec = _spec(frozen.snapshot, strategy, paths)

    def fail():
        raise RuntimeError("original acceptance failure")

    with pytest.raises(RuntimeError):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=frozen.snapshot,
            source=_source(frozen.snapshot),
            artifacts=_artifacts(frozen.snapshot),
            callbacks=replace(_callbacks([]), accept_checks=fail),
            operation_spec=spec,
        )
    receipt = json.loads(
        next(paths.reports_dir.glob("soperator-release-reconcile-*.json")).read_text()
    )
    operation = digest(receipt["operation"]["spec"])
    identity = {"cluster_id": spec.nebius_cluster_id, "kubernetes_uid": spec.kubernetes_uid}
    checks = {
        "schema": "nebius-cxcli.soperator-checks-execution.v2",
        "operation": operation,
        "policy": spec.checks_policy_sha256,
        "phase": "planned",
        "jobs": {},
        "lifecyclePhase": "maintenance",
        "installReservationIntent": True,
        "reservation": "owned",
        "reservationFingerprint": "unchanged",
    }
    scheduling = {
        "schema": "nebius-cxcli.soperator-slurm-recovery.v3",
        "targetRef": "cluster-a",
        "operationSpecSha256": operation,
        "status": "infrastructure-restored",
        "lastCompletedStage": "infrastructure-restored",
        "actions": [],
        "infrastructureRestoreReceipt": {"status": "restored"},
    }
    journal = {
        "schema": "nebius-cxcli.deployment-applications.v1",
        "generation": old.identity,
        "selected": ["cluster-a"],
        "targets": {
            "cluster-a": {
                "identity": identity,
                "status": "executing",
                "desiredBundle": target_bundle_digest(old, "cluster-a"),
            }
        },
    }
    cache = {
        "tenant/project/generated/reports/" + name: encoded(doc)
        for name, doc in {
            "deployment-applications.json": journal,
            "soperator-release-reconcile-prior.json": receipt,
            "soperator-slurm-actions-cluster-a.json": scheduling,
            "soperator-checks-prior.json": checks,
        }.items()
    }
    cache.update(
        {
            "tenant/project/generated/nebius-cxcli-manifest.json": encoded(old.manifest),
            "tenant/project/config.yaml": base64.b64encode(
                yaml.safe_dump(old.manifest["runtime_config"]).encode()
            ).decode(),
        }
    )
    stage = {
        "name": "reconcile",
        "config": old.manifest["runtime_config"],
        "retiredGroups": [],
        "addedGroups": [],
    }
    plan = {
        "controls": {},
        "acceptance": {},
        "semanticPlan": {
            "action": "install",
            "targetRef": "cluster-a",
            "sourceRelease": "",
            "selectedTargets": ["cluster-a"],
            "stages": [stage],
        },
        "admissions": [
            {
                "stage": stage,
                "terraform": {"retain": "all"},
                "application": {"desired": digest(stage["config"])},
            }
        ],
    }
    store = Store()
    state = DeploymentState(store, settings(), assert_held=lambda: None)
    record = state.begin(old, plan=plan, recovery=cache)
    record = state.checkpoint(record, stage="0:reconcile", evidence={"status": "executing"})
    return state, record, new, frozen, paths, receipt, spec, strategy


def test_reconciler_restarts_apply_and_acceptance_with_preserved_predecessor(interrupted):
    _, _, _, frozen, paths, receipt, spec, strategy = interrupted
    calls = []
    result = reconcile_soperator_release(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=frozen.snapshot,
        source=_source(frozen.snapshot),
        artifacts=_artifacts(frozen.snapshot),
        callbacks=_callbacks(calls),
        operation_spec=replace(
            spec, intervention_generation=1, admission_sha256=digest("admission")
        ),
        repair_lineage=SoperatorReconcileRepairLineage(
            predecessor_receipt=receipt,
            previous_operation_spec_sha256=digest(receipt["operation"]["spec"]),
            resume_phase="apply-declarative-release",
            reason=transition.REASON,
        ),
    )
    assert "apply" in calls and "accept-checks" in calls and "final-product" in calls
    assert calls.count("sources") == 1 and "storage" not in calls
    successor = json.loads(result.read_text())
    assert successor["status"] == "complete"
    assert successor["repairLineage"]["predecessorOperationSpecSha256"] == digest(
        receipt["operation"]["spec"]
    )


def successor_intent(interrupted):
    from nebius_cxcli.soperator_receipt_io import write_owner_only_json

    *_, paths, receipt, spec, _ = interrupted
    repair = {
        "schema": transition.REASON,
        "predecessorReceipt": receipt,
        "predecessorReceiptSha256": digest(receipt),
        "previousOperationSpecSha256": digest(receipt["operation"]["spec"]),
        "replacementFiles": {VALUES_FILE: digest("replacement")},
        "reservationHandoff": {"successorPolicy": digest("new checks")},
    }
    successor = replace(
        spec,
        intervention_generation=1,
        desired_values_sha256=repair["replacementFiles"][VALUES_FILE],
        checks_policy_sha256=repair["reservationHandoff"]["successorPolicy"],
        admission_sha256=digest({"installObservabilityRepair": repair}),
    )
    write_owner_only_json(
        paths.reports_dir / "soperator-install-observability-repair-cluster-a.json", repair
    )
    return paths, repair, successor


def test_interruption_after_scheduling_rebind_recovers_pending_successor(interrupted):

    from nebius_cxcli.deployment_recovery import execution_checkpoint
    from nebius_cxcli.soperator_install_observability_repair import seal_successor_intent
    from nebius_cxcli.soperator_install_resume import bound_install_infrastructure_identity

    paths, repair, successor = successor_intent(interrupted)
    published = []
    with execution_checkpoint(lambda: published.append("durable")):
        seal_successor_intent(paths, successor, repair, lambda: None)
    assert published == ["durable"]
    # Model interruption after the scheduling owner binds the new SHA, before
    # either the replacement anchor or the reconcile receipt is created.
    recovered = bound_install_infrastructure_identity(
        paths.reports_dir,
        target_ref=successor.target_ref,
        cluster_id=successor.nebius_cluster_id,
        operation_spec_sha256=digest(asdict(successor)),
    )
    assert recovered == successor.infrastructure_plan_sha256
    seal_successor_intent(paths, successor, repair, lambda: None)
    with pytest.raises(RuntimeError, match="lineage|checkpoint"):
        seal_successor_intent(
            paths, replace(successor, scheduling_sha256=digest("unrelated")), repair, lambda: None
        )


@pytest.mark.parametrize("change", ["spec", "repair", "cluster", "generation", "hash"])
def test_pending_successor_cannot_authorize_another_operation(interrupted, change):

    from nebius_cxcli.soperator_install_observability_repair import seal_successor_intent
    from nebius_cxcli.soperator_install_resume import bound_install_infrastructure_identity
    from nebius_cxcli.soperator_receipt_io import read_owner_only_json, write_owner_only_json

    paths, repair, successor = successor_intent(interrupted)
    seal_successor_intent(paths, successor, repair, lambda: None)
    path = paths.reports_dir / "soperator-recovery-observability-successor-cluster-a.json"
    intent = read_owner_only_json(path, label="test intent")
    if change == "spec":
        intent["spec"]["infrastructure_plan_sha256"] = digest("other")
    if change == "repair":
        intent["repairSha256"] = digest("other")
    if change == "generation":
        intent["spec"]["intervention_generation"] += 1
    write_owner_only_json(path, intent)
    with pytest.raises(RuntimeError):
        bound_install_infrastructure_identity(
            paths.reports_dir,
            target_ref=successor.target_ref,
            cluster_id="other" if change == "cluster" else successor.nebius_cluster_id,
            operation_spec_sha256=digest("other")
            if change == "hash"
            else digest(asdict(successor)),
        )


def test_native_check_policy_rebinds_only_its_own_marker():
    from nebius_cxcli.soperator_checks_policy import SoperatorChecksPolicy, checks_digest
    from nebius_cxcli.soperator_install_observability_repair import assert_same_diagnostic_policy

    before = SoperatorChecksPolicy(digest("source"), checks_digest({"before": True}), (), {})
    after_values = {"after": True}
    after = replace(before, values_sha256=checks_digest(after_values))

    def specs(sha):
        return {
            "native": {
                "jobContainer": {
                    "env": [
                        {"name": "CXCLI_CHECK_POLICY_SHA256", "value": sha},
                        {"name": "KEEP", "value": "unchanged"},
                    ]
                }
            }
        }

    before = replace(before, execution_specs=specs(before.sha256))
    after = replace(after, execution_specs=specs(after.sha256))
    assert_same_diagnostic_policy(before, after, after_values)
    changed = copy.deepcopy(after.execution_specs)
    changed["native"]["jobContainer"]["env"][1]["value"] = "changed"
    with pytest.raises(RuntimeError, match="diagnostic policy"):
        assert_same_diagnostic_policy(before, replace(after, execution_specs=changed), after_values)


def test_raw_recovery_chart_binding_requires_the_exact_frozen_digest():
    source = {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "HelmRepository",
        "metadata": {"name": "gpu", "namespace": "flux-system"},
        "spec": {"type": "oci", "url": "oci://registry.example.com/gpu", "interval": "1m"},
    }
    release = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "gpu", "namespace": "flux-system"},
        "spec": {
            "chart": {
                "spec": {
                    "chart": "operator",
                    "version": "1.0",
                    "sourceRef": {"name": "gpu", "kind": "HelmRepository"},
                }
            },
            "values": {"driver": {"enabled": False}},
        },
    }
    raw = {
        "ordinary/source.yaml": yaml.safe_dump(source).encode(),
        "ordinary/release.yaml": yaml.safe_dump(release).encode(),
    }
    frozen = {
        "gpu": {
            "reference": {
                "chart_repo": "oci://registry.example.com/gpu",
                "chart_name": "operator",
                "chart_version": "1.0",
            },
            "oci_digest": digest("chart"),
        }
    }
    bound = transition.bound_artifact_files(raw, frozen)
    assert yaml.safe_load(bound["ordinary/source.yaml"])["spec"]["ref"] == {
        "digest": digest("chart")
    }
    assert (
        yaml.safe_load(bound["ordinary/release.yaml"])["spec"]["values"]
        == release["spec"]["values"]
    )
    assert transition.bound_artifact_files(bound, frozen) == bound
    with pytest.raises(ValueError, match="frozen chart"):
        transition.bound_artifact_files(raw, {})
