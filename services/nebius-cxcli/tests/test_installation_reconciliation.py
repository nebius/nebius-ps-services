import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from nebius_cxcli import installation_reconciliation as reconcile
from nebius_cxcli.deployment_state import DeploymentGeneration, DeploymentState, digest
from nebius_cxcli.ordinary_apps import (
    _validate_live_lifecycle_complete,
    validate_accepted_deployment_record,
)
from nebius_cxcli.soperator_operation import SOPERATOR_OPERATION_ANCHOR_SCHEMA
from test_deployment_state import Store, settings


@pytest.fixture
def installation(monkeypatch):
    identity = {"cluster_id": "cluster", "kubernetes_uid": "kube-uid"}
    state = DeploymentState(Store(), settings(), assert_held=lambda: None)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    begun = state.begin(generation, plan={})
    state.accept(
        begun,
        evidence={
            "release": "4.1.8",
            "identities": {"cluster": identity},
            "targets": {"cluster": {"identity": identity}},
        },
    )
    bindings = {
        "ownership": "managed",
        "sourceContract": "absent",
        "targetContract": "upstream-flux-v1",
        "targetCapabilitySha256": digest("capability"),
        "releaseSnapshotSha256": digest("snapshot"),
        "targetJailImage": "registry.example.invalid/jail@sha256:" + "a" * 64,
        "targetJailImageSource": "upstream-default",
    }
    monkeypatch.setattr(reconcile, "accepted_install_identity", lambda *a: bindings)
    cluster = hashlib.sha256(b"cluster").hexdigest()[:10]
    objects = {}
    for index in (1, 2):
        op = digest(index)
        name = f"nebius-cxcli-soperator-op-{cluster}-{op.removeprefix('sha256:')[:10]}"
        objects[name] = {
            "metadata": {
                "name": name,
                "uid": f"uid-{index}",
                "resourceVersion": "1",
                "labels": {"app.kubernetes.io/managed-by": "nebius-cxcli"},
            },
            "data": {
                **bindings,
                "sourceCapabilitySha256": digest("absent"),
                "stagePlanSha256": digest("stages"),
                "admissionSha256": digest("admission"),
                "infrastructureReceiptSha256": digest("infra"),
                "holderIdentitySha256": digest("holder"),
                "leaseName": "lease",
                "leaseUid": "lease-uid",
                "fencingEpoch": "1",
                "schema": SOPERATOR_OPERATION_ANCHOR_SCHEMA,
                "operationId": op,
                "operationSpecSha256": op,
                "clusterId": "cluster",
                "kubernetesUid": "kube-uid",
                "targetRef": "cluster",
                "targetRelease": "4.1.8",
                "currentRelease": "",
                "status": "active",
                "strategy": "install",
            },
        }
    calls = []

    def read(argv, env):
        calls.append(argv)
        if argv[1] == "configmaps":
            return {"items": copy.deepcopy(list(objects.values()))}
        if argv[1] in {"jobs", "pods"}:
            return {"items": []}
        item = objects[argv[2]]
        if argv[0] == "patch":
            patch = json.loads(argv[argv.index("-p") + 1])
            assert patch[0]["value"] == item["metadata"]["uid"]
            assert patch[1]["value"] == item["metadata"]["resourceVersion"]
            assert patch[2]["value"] == item["data"]
            item["data"] = patch[3]["value"]
            item["metadata"]["resourceVersion"] = "2"
        return copy.deepcopy(item)

    monkeypatch.setattr(reconcile, "_read", read)
    monkeypatch.setattr(reconcile, "observe_accepted", lambda *a: {"ready": True, "writers": {}})
    console = SimpleNamespace(print=lambda *a: None)

    def run(fence=lambda: None):
        return reconcile.reconcile_accepted_installation(
            SimpleNamespace(console=console),
            state,
            target_ref="cluster",
            env={},
            fence=fence,
        )

    return state, objects, calls, run, identity


def test_authority_lost_during_anchor_read_blocks_patch(installation, monkeypatch):
    state, objects, calls, run, _ = installation
    original = reconcile._read
    held = True

    def read(argv, env):
        nonlocal held
        result = original(argv, env)
        if argv[:2] == ["get", "configmap"]:
            held = False
        return result

    def fence():
        if not held:
            raise RuntimeError("authority lost during anchor read")

    monkeypatch.setattr(reconcile, "_read", read)
    with pytest.raises(RuntimeError, match="authority lost during anchor read"):
        run(fence)
    assert all(call[0] != "patch" for call in calls)
    assert all(item["data"]["status"] == "active" for item in objects.values())
    assert state.read().value["active"] is not None


def test_historical_installation_reconciles_without_inventing_success_and_replays_noop(
    installation, monkeypatch
):
    state, objects, calls, run, identity = installation
    before = copy.deepcopy(state.read().value["accepted"])
    record = run()
    assert record.value["active"] is None
    assert record.value["accepted"]["generation"] == before["generation"]
    assert record.value["accepted"]["evidence"]["targets"] == before["evidence"]["targets"]
    assert all(o["data"]["status"] == "reconciled" for o in objects.values())
    key = next(iter(record.value["accepted"]["evidence"][reconcile.RECEIPTS]))
    receipt = state.store.read(
        state.prefix + "/installation-reconciliations/" + key.removeprefix("sha256:")
    ).value
    assert all(row["data"]["status"] == "active" for row in receipt["originals"])
    for item in objects.values():
        reconcile.validate_reconciled(item, state, record)
    monkeypatch.setattr(
        "nebius_cxcli.ordinary_apps._live_json", lambda *a: {"items": list(objects.values())}
    )
    _validate_live_lifecycle_complete(identity, {}, state=state)
    writes = state.store.next_etag
    calls.clear()
    assert run().value == record.value
    assert state.store.next_etag == writes and all(c[0] != "patch" for c in calls)


@pytest.mark.parametrize("boundary", ["before-first", "after-first", "after-second", "commit"])
def test_same_command_resumes_partial_reconciliation(installation, monkeypatch, boundary):
    state, objects, _, run, _ = installation
    original = reconcile._read
    patches = 0

    def interrupted(argv, env):
        nonlocal patches
        if argv[0] == "patch":
            patches += 1
            if boundary == "before-first" and patches == 1:
                raise RuntimeError("interrupted")
        result = original(argv, env)
        if argv[0] == "patch" and patches == {"after-first": 1, "after-second": 2}.get(boundary):
            raise RuntimeError("interrupted")
        return result

    completion = state.complete_installation_reconciliation
    if boundary == "commit":
        monkeypatch.setattr(
            state,
            "complete_installation_reconciliation",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("interrupted")),
        )
    monkeypatch.setattr(reconcile, "_read", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        run()
    pending = state.read()
    assert reconcile.pending_reconciliation(pending.value, "cluster")
    for item in objects.values():
        if item["data"]["status"] == "reconciled":
            with pytest.raises(RuntimeError, match="not committed"):
                reconcile.validate_reconciled(item, state, pending)
    baseline = {
        "deployment_generation": pending.value["accepted"]["generation"],
        "identities": pending.value["accepted"]["evidence"]["identities"],
    }
    validate_accepted_deployment_record(pending.value, baseline, ["cluster"])
    monkeypatch.setattr(reconcile, "_read", original)
    monkeypatch.setattr(state, "complete_installation_reconciliation", completion)
    assert run().value["active"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("strategy", "upgrade"),
        ("currentRelease", "4.1.7"),
        ("targetRelease", "4.1.9"),
        ("kubernetesUid", "foreign"),
        ("operationSpecSha256", digest("foreign")),
        ("targetRef", "other"),
        ("ownership", "external"),
        ("targetCapabilitySha256", digest("foreign")),
        ("releaseSnapshotSha256", digest("foreign")),
        ("targetJailImage", "foreign"),
        ("sourceContract", "present"),
        ("sourceCapabilitySha256", ""),
        ("leaseUid", ""),
    ],
)
def test_foreign_or_non_install_history_blocks_before_any_write(installation, field, value):
    state, objects, calls, run, _ = installation
    next(iter(objects.values()))["data"][field] = value
    before = state.store.next_etag
    with pytest.raises(RuntimeError, match="outside accepted"):
        run()
    assert state.store.next_etag == before and all(c[0] != "patch" for c in calls)


def test_fresh_observation_failure_never_changes_history(installation, monkeypatch):
    state, objects, _, run, _ = installation
    before = state.store.next_etag
    monkeypatch.setattr(
        reconcile,
        "observe_accepted",
        lambda *a: (_ for _ in ()).throw(RuntimeError("storage drift")),
    )
    with pytest.raises(RuntimeError, match="storage drift"):
        run()
    assert state.store.next_etag == before and all(
        o["data"]["status"] == "active" for o in objects.values()
    )


@pytest.mark.parametrize("kind", ["jobs", "pods"])
def test_unfenced_prior_writer_blocks_even_with_released_lease(monkeypatch, kind):
    writer = {
        "metadata": {"name": "cxcli-install", "uid": "writer"},
        "status": {"phase": "Running", "active": 1},
    }
    monkeypatch.setattr(
        reconcile, "_read", lambda argv, env: {"items": [writer] if argv[1] == kind else []}
    )
    with pytest.raises(RuntimeError, match="not provably stopped"):
        reconcile.assert_quiescent({})


@pytest.mark.parametrize("kind", ["pods", "jobs"])
def test_unlabeled_rootfs_writer_is_scoped_by_namespace(monkeypatch, kind):
    pod = {"volumes": [{"name": "jail", "persistentVolumeClaim": {"claimName": "jail-pvc"}}]}
    writer = {
        "metadata": {"name": "unrelated-name", "namespace": "soperator", "uid": "writer"},
        "spec": pod if kind == "pods" else {"template": {"spec": pod}},
        "status": {"phase": "Running", "active": 1},
    }
    monkeypatch.setattr(
        reconcile, "_read", lambda argv, env: {"items": [writer] if argv[1] == kind else []}
    )
    storage = {"volumes": {"jail-pvc": {}}}
    with pytest.raises(RuntimeError, match="not provably stopped"):
        reconcile.assert_quiescent({}, storage, namespace="soperator")
    writer["metadata"]["namespace"] = "other"
    assert reconcile.assert_quiescent({}, storage, namespace="soperator") == {}


def test_only_exact_observed_slurm_controller_can_exempt_a_running_rootfs_pod(monkeypatch):
    controller = {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": {"name": "login", "namespace": "soperator", "uid": "controller"},
    }
    pod = {
        "metadata": {
            "uid": "pod",
            "namespace": "soperator",
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "StatefulSet",
                    "uid": "controller",
                    "controller": True,
                }
            ],
        },
        "spec": {"volumes": [{"persistentVolumeClaim": {"claimName": "jail-pvc"}}]},
        "status": {"phase": "Running"},
    }
    monkeypatch.setattr(
        reconcile, "_read", lambda argv, env: {"items": [pod] if argv[1] == "pods" else []}
    )
    kwargs = dict(namespace="soperator", steady_controllers=[controller])
    assert reconcile.assert_quiescent({}, {"volumes": {"jail-pvc": {}}}, **kwargs) == {}
    pod["metadata"]["ownerReferences"][0]["uid"] = "replacement"
    with pytest.raises(RuntimeError, match="not provably stopped"):
        reconcile.assert_quiescent({}, {"volumes": {"jail-pvc": {}}}, **kwargs)


@pytest.mark.parametrize(
    "writable_container",
    [None, "containers", "initContainers", "ephemeralContainers", "device", "unmounted"],
)
def test_read_only_report_viewers_are_not_prior_writers(monkeypatch, writable_container):
    spec = {
        "volumes": [{"name": "reports", "persistentVolumeClaim": {"claimName": "jail-pvc"}}],
        "containers": [{"name": "viewer", "volumeMounts": [{"name": "reports", "readOnly": True}]}],
    }
    if writable_container in {"containers", "initContainers", "ephemeralContainers"}:
        spec.setdefault(writable_container, []).append(
            {"name": "writer", "volumeMounts": [{"name": "reports"}]}
        )
    elif writable_container == "device":
        spec["containers"][0]["volumeDevices"] = [{"name": "reports", "devicePath": "/dev/report"}]
    elif writable_container == "unmounted":
        spec["containers"][0]["volumeMounts"] = []
    pod = {
        "metadata": {"name": "report-viewer", "namespace": "soperator", "uid": "viewer"},
        "spec": spec,
        "status": {"phase": "Running"},
    }
    monkeypatch.setattr(
        reconcile, "_read", lambda argv, env: {"items": [pod] if argv[1] == "pods" else []}
    )
    if writable_container:
        with pytest.raises(RuntimeError, match="writer has not provably stopped"):
            reconcile.assert_quiescent({}, {"volumes": {"jail-pvc": {}}}, namespace="soperator")
    else:
        assert (
            reconcile.assert_quiescent({}, {"volumes": {"jail-pvc": {}}}, namespace="soperator")
            == {}
        )


def test_accepted_binding_comes_from_authenticated_generation(monkeypatch):
    import base64

    snapshot = {
        "release": "4.1.8",
        "capability_contract": "contract",
        "capability_sha256": digest("cap"),
        "snapshot_sha256": digest("snapshot"),
        "populate_jail_image": "image",
    }
    generation = DeploymentGeneration(
        {"deploy": {"targets": [{"target_ref": "cluster", "ownership": "managed"}]}},
        {
            "reports/soperator-release-snapshot-cluster.json": base64.b64encode(
                json.dumps(snapshot).encode()
            ).decode()
        },
    )
    monkeypatch.setattr(
        "nebius_cxcli.deployment_jail_state.accepted_effective_generation", lambda *a: generation
    )
    accepted = {"evidence": {"release": "4.1.8", "targets": {"cluster": {}}}}
    assert reconcile.accepted_install_identity(None, accepted, "cluster")[
        "releaseSnapshotSha256"
    ] == digest("snapshot")
    accepted["evidence"]["release"] = "4.1.9"
    with pytest.raises(RuntimeError, match="ambiguous"):
        reconcile.accepted_install_identity(None, accepted, "cluster")


@pytest.mark.parametrize("pod_owner_uid", ["adapter-uid", "foreign-uid"])
def test_accepted_observation_uses_verified_adapter_resource_evidence(monkeypatch, pod_owner_uid):
    from nebius_cxcli import (
        deployment_jail_state,
        deployment_observation,
        soperator_status_collect,
        soperator_status_health,
    )

    adapter = {
        "apiVersion": "apps/v1",
        "kind": "DaemonSet",
        "metadata": {"name": "adapter", "namespace": "soperator"},
        "spec": {},
    }
    live_adapter = copy.deepcopy(adapter)
    live_adapter["metadata"]["uid"] = "adapter-uid"
    cluster = {
        "kind": "SlurmCluster",
        "metadata": {
            "name": "slurm",
            "namespace": "soperator",
            "uid": "cluster-uid",
            "annotations": {
                "meta.helm.sh/release-name": "slurm",
                "meta.helm.sh/release-namespace": "soperator",
            },
        },
    }
    pod = {
        "metadata": {
            "name": "adapter-pod",
            "namespace": "soperator",
            "uid": "pod-uid",
            "labels": {"app.kubernetes.io/managed-by": "nebius-cxcli"},
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "DaemonSet",
                    "uid": pod_owner_uid,
                    "controller": True,
                }
            ],
        },
        "status": {"phase": "Running"},
    }
    storage = {"activePvcName": "jail", "volumes": {"jail": {}}}
    accepted = {
        "evidence": {
            "targets": {
                "cluster": {
                    "identity": {"cluster_id": "cluster", "kubernetes_uid": "kube-uid"},
                    "jailState": {"storageEvidence": storage},
                }
            }
        }
    }
    generation = SimpleNamespace(manifest={"runtime_config": {}}, materialize=lambda paths: None)
    monkeypatch.setattr(
        deployment_jail_state, "accepted_effective_generation", lambda *a: generation
    )
    monkeypatch.setattr(deployment_jail_state, "jail_values", lambda *a: {})
    monkeypatch.setattr(deployment_jail_state, "observe_jail_storage", lambda *a, **kw: storage)
    monkeypatch.setattr(
        deployment_observation, "target_documents", lambda *a, **kw: {"adapter": adapter}
    )
    monkeypatch.setattr(
        soperator_status_collect,
        "collect_status_snapshot",
        lambda **kw: {
            "soperator_resources": [cluster],
            "workloads": [live_adapter],
        },
    )
    monkeypatch.setattr(
        soperator_status_health,
        "project_status_health",
        lambda snapshot: SimpleNamespace(overall="Healthy"),
    )
    monkeypatch.setattr(
        reconcile, "accepted_slurm_release_owners", lambda *a: ({("slurm", "soperator")}, {})
    )
    monkeypatch.setattr(
        reconcile, "_read", lambda argv, env: {"items": [pod] if argv[1] == "pods" else []}
    )
    cli = SimpleNamespace(
        console=SimpleNamespace(print=lambda *a: None),
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="CONTEXT",
        _run_soperator_upgrade_kubectl=lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps(live_adapter)
        ),
    )
    if pod_owner_uid != "adapter-uid":
        with pytest.raises(RuntimeError, match="writer has not provably stopped"):
            reconcile.observe_accepted(
                cli, None, accepted, "cluster", {"CONTEXT": "verified"}, lambda: None
            )
    else:
        proof = reconcile.observe_accepted(
            cli, None, accepted, "cluster", {"CONTEXT": "verified"}, lambda: None
        )
        assert proof["ready"] and proof["writers"] == {}
        assert proof["resources"]["resources"]["adapter"]["uid"] == "adapter-uid"


def test_steady_slurm_ownership_includes_deployment_replicas_and_chart_owned_nodesets(monkeypatch):
    def resource(kind, name, parent=None, *, helm=False):
        value = {
            "kind": kind,
            "apiVersion": "apps/v1"
            if kind in {"Deployment", "ReplicaSet", "StatefulSet"}
            else "slurm.nebius.ai/v1alpha1",
            "metadata": {"name": name, "uid": name, "namespace": "soperator"},
        }
        if parent:
            value["metadata"]["ownerReferences"] = [
                {
                    "uid": parent["metadata"]["uid"],
                    "kind": parent["kind"],
                    "apiVersion": parent["apiVersion"],
                    "controller": True,
                }
            ]
        if helm:
            value["metadata"]["annotations"] = {
                "meta.helm.sh/release-name": "slurm",
                "meta.helm.sh/release-namespace": "soperator",
            }
        return value

    cluster = resource("SlurmCluster", "cluster", helm=True)
    node = resource("NodeSet", "worker", helm=True)
    node["metadata"]["annotations"]["slurm.nebius.ai/parental-cluster-ref"] = "cluster"
    deployment = resource("Deployment", "accounting", cluster)
    replica = resource("ReplicaSet", "accounting-replica", deployment)
    worker = resource("StatefulSet", "worker-set", node)
    alien = resource("StatefulSet", "alien")
    snapshot = {
        "soperator_resources": [cluster, node],
        "workloads": [deployment, replica, worker, alien],
    }
    monkeypatch.setattr(
        "nebius_cxcli.deployment_observation.target_documents",
        lambda *a, **kw: {
            "hr": {
                "kind": "HelmRelease",
                "spec": {"releaseName": "slurm", "targetNamespace": "soperator"},
            }
        },
    )
    assert {
        r["metadata"]["name"]
        for r in reconcile.steady_slurm_controllers(snapshot, {("slurm", "soperator")}, {})
    } == {"accounting", "accounting-replica", "worker-set"}
    assert alien in reconcile.steady_slurm_controllers(
        snapshot, {("slurm", "soperator")}, {"declared-adapter": {"uid": "alien"}}
    )
    assert alien not in reconcile.steady_slurm_controllers(
        snapshot, {("slurm", "soperator")}, {"declared-adapter": {"uid": "replacement"}}
    )
    node["metadata"]["annotations"]["meta.helm.sh/release-name"] = "foreign"
    assert worker not in reconcile.steady_slurm_controllers(snapshot, {("slurm", "soperator")}, {})
    cluster["metadata"]["annotations"]["meta.helm.sh/release-name"] = "foreign"
    with pytest.raises(RuntimeError, match="SlurmCluster ownership"):
        reconcile.steady_slurm_controllers(snapshot, {("slurm", "soperator")}, {})


@pytest.mark.parametrize(
    "fault", [None, "owner", "source", "generation", "namespace", "suspended", "missing"]
)
def test_child_helm_owners_require_the_exact_accepted_graph(monkeypatch, fault):
    graph = [
        {
            "releaseName": "child-" + kind,
            "upstreamReleaseName": "soperator-fluxcd-" + kind,
            "sourceKind": "OCIRepository",
            "sourceName": "source-" + kind,
            "namespace": "flux-system",
        }
        for kind in ("slurm-cluster", "nodesets")
    ]
    outer = {
        "kind": "HelmRelease",
        "metadata": {"name": "outer", "namespace": "flux-system"},
        "spec": {},
    }
    documents = {
        "outer": outer,
        "graph": {
            "kind": "ConfigMap",
            "metadata": {"name": "nebius-cxcli-soperator-release-graph"},
            "data": {"graph.json": json.dumps({"releases": graph})},
        },
    }
    documents["pvc"] = {
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": "jail", "namespace": "soperator"},
    }
    items = [
        {
            "metadata": {
                "name": row["releaseName"],
                "namespace": "flux-system",
                "uid": row["releaseName"],
                "generation": 2,
                "annotations": {
                    "meta.helm.sh/release-name": "outer",
                    "meta.helm.sh/release-namespace": "flux-system",
                },
            },
            "spec": {
                "releaseName": row["releaseName"],
                "targetNamespace": "soperator",
                "chartRef": {"kind": "OCIRepository", "name": row["sourceName"]},
            },
            "status": {
                "observedGeneration": 2,
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        }
        for row in graph
    ]
    if fault == "owner":
        items[0]["metadata"]["annotations"]["meta.helm.sh/release-name"] = "other"
    if fault == "source":
        items[0]["spec"]["chartRef"]["name"] = "other"
    if fault == "generation":
        items[0]["metadata"]["generation"] = 3
    if fault == "namespace":
        items[0]["spec"]["targetNamespace"] = "other"
    if fault == "suspended":
        items[0]["spec"]["suspend"] = True
    if fault == "missing":
        items.pop()
    monkeypatch.setattr(
        "nebius_cxcli.deployment_observation.target_documents", lambda *a, **kw: documents
    )
    monkeypatch.setattr("nebius_cxcli.flux_ops._staged_soperator_outer_release", lambda *a: outer)
    monkeypatch.setattr(reconcile, "_read", lambda *a: {"items": items})
    if fault:
        with pytest.raises(RuntimeError, match="Accepted Slurm child release"):
            reconcile.accepted_slurm_release_owners(
                object(), "target", {}, {"activePvcName": "jail"}
            )
    else:
        owners, evidence = reconcile.accepted_slurm_release_owners(
            object(), "target", {}, {"activePvcName": "jail"}
        )
        assert len(owners) == len(evidence) == 2
