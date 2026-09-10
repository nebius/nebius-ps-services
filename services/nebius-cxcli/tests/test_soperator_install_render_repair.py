import copy
import json

import pytest
import yaml

from nebius_cxcli import soperator_install_render_repair as repair

DIGEST = "sha256:" + "a" * 64


def encode(*documents):
    return yaml.safe_dump_all(documents, sort_keys=False).encode()


@pytest.fixture
def render_delta():
    values = {
        "soperator": {"monitoringDashboards": {"enabled": True, "version": "4.1.5"}},
        "storage": {"jail": "retain"},
    }
    cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "values"},
        "data": {"values.yaml": yaml.safe_dump(values)},
    }
    child_patch = {
        "target": {
            "group": "helm.toolkit.fluxcd.io",
            "version": "v2",
            "kind": "HelmRelease",
            "name": repair.UPSTREAM_DASHBOARD_RELEASE,
        },
        "patch": "owned dashboard wiring",
    }
    outer = {
        "kind": "HelmRelease",
        "spec": {
            "values": values,
            "postRenderers": [
                {
                    "kustomize": {
                        "patches": [child_patch, {"target": {"name": "keep"}, "patch": "keep"}]
                    }
                }
            ],
        },
    }
    source = {
        "kind": "OCIRepository",
        "metadata": {"name": repair.DASHBOARD_SOURCE, "namespace": "flux-system"},
        "spec": {"ref": {"digest": DIGEST}},
    }
    row = {
        "releaseName": repair.DASHBOARD_RELEASE,
        "revision": DIGEST,
        "sourceName": repair.DASHBOARD_SOURCE,
        "isMain": False,
        "dependencies": ["namespace"],
    }
    contract = {
        "releases": [row, {"releaseName": "main", "isMain": True, "dependencies": []}],
        "readiness": {"storage": "retain"},
    }
    graph = {
        "kind": "ConfigMap",
        "metadata": {"name": "nebius-cxcli-soperator-release-graph"},
        "data": {"graph.json": json.dumps(contract, sort_keys=True, separators=(",", ":"))},
    }
    before = {
        repair.VALUES_FILE: encode(cm),
        repair.OUTER_FILE: encode(outer),
        repair.GRAPH_FILE: encode(source, graph),
        "ordinary/app.yaml": b"unchanged",
    }
    dashboards = [
        {
            "kind": "ConfigMap",
            "metadata": {"name": f"dashboard-{n}"},
            "data": {"dashboard.json": "{}"},
        }
        for n in range(7)
    ]
    values["soperator"]["monitoringDashboards"] = {"enabled": False}
    cm["data"]["values.yaml"] = yaml.safe_dump(values)
    outer["spec"]["postRenderers"][0]["kustomize"]["patches"].remove(child_patch)
    contract["releases"].remove(row)
    graph["data"]["graph.json"] = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    after = {
        repair.VALUES_FILE: encode(cm),
        repair.OUTER_FILE: encode(outer),
        repair.GRAPH_FILE: encode(graph),
        repair.DASHBOARD_FILE: encode(*dashboards),
        "ordinary/app.yaml": b"unchanged",
    }
    return before, after, dashboards


def validate(before, after, dashboards):
    repair.validate_dashboard_render_delta(
        before, after, release="4.1.5", chart_digest=DIGEST, expected_dashboards=dashboards
    )


def test_closed_dashboard_delta_preserves_all_other_values_and_apps(render_delta):
    validate(*render_delta)


@pytest.mark.parametrize(
    "mutation", ["ordinary", "dashboard", "values", "outer", "graph", "remove", "add"]
)
def test_dashboard_delta_rejects_unrelated_mutations(render_delta, mutation):
    before, after, dashboards = copy.deepcopy(render_delta)
    if mutation == "ordinary":
        after["ordinary/app.yaml"] = b"changed"
    elif mutation == "dashboard":
        after[repair.DASHBOARD_FILE] = encode(*dashboards[:6])
    elif mutation == "values":
        cm = yaml.safe_load(after[repair.VALUES_FILE])
        values = yaml.safe_load(cm["data"]["values.yaml"])
        values["storage"]["jail"] = "foreign"
        cm["data"]["values.yaml"] = yaml.safe_dump(values)
        after[repair.VALUES_FILE] = encode(cm)
    elif mutation == "outer":
        doc = yaml.safe_load(after[repair.OUTER_FILE])
        doc["spec"]["suspend"] = False
        after[repair.OUTER_FILE] = encode(doc)
    elif mutation == "graph":
        doc = yaml.safe_load(after[repair.GRAPH_FILE])
        c = json.loads(doc["data"]["graph.json"])
        c["readiness"]["storage"] = "foreign"
        doc["data"]["graph.json"] = json.dumps(c)
        after[repair.GRAPH_FILE] = encode(doc)
    elif mutation == "remove":
        del after["ordinary/app.yaml"]
    else:
        after["extra.yaml"] = b"new"
    with pytest.raises(RuntimeError):
        validate(before, after, dashboards)


@pytest.fixture
def failed_child():
    return {
        "metadata": {
            "name": repair.DASHBOARD_RELEASE,
            "namespace": "flux-system",
            "uid": "dashboard-uid",
            "generation": 1,
            "labels": {
                "soperator.nebius.ai/release-graph": "nebius-cxcli",
                "app.kubernetes.io/version": "4.1.5",
            },
        },
        "spec": {"chartRef": {"kind": "OCIRepository", "name": repair.DASHBOARD_SOURCE}},
        "status": {
            "observedGeneration": 1,
            "lastAttemptedRevisionDigest": DIGEST,
            "history": None,
            "conditions": [
                {
                    "type": "Ready",
                    "observedGeneration": 1,
                    "status": "False",
                    "reason": "InstallFailed",
                    "message": "error: invalid document separator: ---apiVersion: v1",
                }
            ],
        },
    }


def test_failed_dashboard_requires_exact_current_uninstalled_chart(failed_child):
    assert (
        repair.validate_failed_dashboard(failed_child, release="4.1.5", source_digest=DIGEST)
        == "dashboard-uid"
    )


def test_retrying_failure_uses_current_condition_generation(failed_child):
    failed_child["metadata"]["generation"] = 2
    failed_child["status"]["conditions"][0]["observedGeneration"] = 2
    assert (
        repair.validate_failed_dashboard(failed_child, release="4.1.5", source_digest=DIGEST)
        == "dashboard-uid"
    )
    failed_child["status"]["conditions"][0]["observedGeneration"] = 1
    with pytest.raises(RuntimeError):
        repair.validate_failed_dashboard(failed_child, release="4.1.5", source_digest=DIGEST)


@pytest.mark.parametrize(
    "mutation",
    [
        "name",
        "namespace",
        "uid",
        "owner",
        "version",
        "generation",
        "digest",
        "success",
        "terminating",
        "source",
        "failure",
    ],
)
def test_failed_dashboard_rejects_foreign_or_successful_history(failed_child, mutation):
    m = failed_child["metadata"]
    s = failed_child["status"]
    if mutation in {"name", "namespace", "uid"}:
        m[mutation] = ""
    elif mutation == "owner":
        m["labels"]["soperator.nebius.ai/release-graph"] = "foreign"
    elif mutation == "version":
        m["labels"]["app.kubernetes.io/version"] = "4.1.7"
    elif mutation == "generation":
        m["generation"] = 2
    elif mutation == "digest":
        s["lastAttemptedRevisionDigest"] = DIGEST + "extra"
    elif mutation == "success":
        s["history"] = [{"status": "deployed"}]
    elif mutation == "terminating":
        m["deletionTimestamp"] = "now"
    elif mutation == "source":
        failed_child["spec"]["chartRef"]["name"] = "foreign"
    else:
        s["conditions"][0]["message"] = "different failure"
    with pytest.raises(RuntimeError):
        repair.validate_failed_dashboard(failed_child, release="4.1.5", source_digest=DIGEST)


def test_candidate_uses_frozen_values_and_preserves_every_unrelated_byte(render_delta):
    before, _, dashboards = render_delta
    actual = repair.dashboard_repair_candidate(
        before, release="4.1.5", chart_digest=DIGEST, dashboards=dashboards
    )
    validate(before, actual, dashboards)
    assert actual["ordinary/app.yaml"] == before["ordinary/app.yaml"]


@pytest.mark.parametrize(
    "mutation", [None, "changed-local-receipt", "missing-seal", "wrong-cluster", "wrong-fence"]
)
@pytest.mark.parametrize("generation", [1, 2])
def test_saved_repair_requires_cluster_bound_admission(monkeypatch, tmp_path, mutation, generation):
    from dataclasses import asdict, replace

    from nebius_cxcli.soperator_operation_lock import SoperatorLeaseAuthority
    from nebius_cxcli.soperator_release_reconciler import resolve_soperator_reconcile_strategy
    from soperator_fixtures import sample_snapshot
    from test_soperator_release_reconciler import _paths, _spec

    snapshot = sample_snapshot()
    strategy = resolve_soperator_reconcile_strategy(
        current_release=None,
        target_release=snapshot.release,
        source_contract=None,
        target_contract=snapshot.capability_contract,
    )
    spec = replace(
        _spec(snapshot, strategy, _paths(tmp_path)), intervention_generation=generation - 1
    )
    predecessor = {"operation": {"spec": asdict(spec)}}
    saved = {
        "schema": repair.REPAIR_REASON if generation == 1 else repair.CHECKS_REPAIR_REASON,
        "predecessorReceipt": predecessor,
        "predecessorReceiptSha256": repair._digest(predecessor),
        "previousOperationSpecSha256": repair._digest(asdict(spec)),
        "interventionGeneration": generation,
    }
    seal = "installDashboardRepairSha256" if generation == 1 else "installChecksRepairSha256"
    data = {
        "operationSpecSha256": saved["previousOperationSpecSha256"],
        "clusterId": spec.nebius_cluster_id,
        "kubernetesUid": spec.kubernetes_uid,
        "status": "superseded",
        seal: repair._digest(saved),
    }
    authority = SoperatorLeaseAuthority("lease", "uid", "a" * 64, 2, "fingerprint")
    if mutation == "changed-local-receipt":
        saved["replacementFiles"] = {"foreign.yaml": "changed"}
    elif mutation == "missing-seal":
        del data[seal]
    elif mutation == "wrong-cluster":
        data["clusterId"] = "foreign"
    elif mutation == "wrong-fence":
        authority = object()
    monkeypatch.setattr(repair, "_kube_get", lambda *a, **kw: {"data": data})
    monkeypatch.setattr(
        repair.subprocess, "run", lambda *a, **kw: pytest.fail("reload must not mutate")
    )
    args = dict(env={}, kube_context="explicit", assert_authority=lambda: authority, create=False)
    if mutation is None:
        repair._bind_repair_admission(saved, **args)
    else:
        with pytest.raises(RuntimeError):
            repair._bind_repair_admission(saved, **args)
