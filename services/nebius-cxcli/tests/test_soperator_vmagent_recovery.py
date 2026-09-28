import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from nebius_cxcli import soperator_vmagent_recovery as repair
from nebius_cxcli.soperator_failures import SoperatorSafetyPauseError
from nebius_cxcli.soperator_graph_transition import HR, SCHEMA, NativeGraphTransition
from nebius_cxcli.soperator_operation import soperator_sha256

VERIFY_DESIRED_AGENT = repair.verify_desired_agent


@pytest.fixture
def recovery(monkeypatch):
    metadata = {
        "name": "metrics",
        "namespace": "flux-system",
        "uid": "child-uid",
        "resourceVersion": "1",
        "generation": 1,
    }
    common = {
        "name": "metrics",
        "namespace": "flux-system",
        "chartName": "metrics",
        "chartVersion": "1.0.0",
        "configDigest": "sha256:desired",
    }
    failed = {**common, "version": 2, "status": "failed", "action": "upgrade"}
    rollback = {
        **common,
        "version": 3,
        "status": "deployed",
        "action": "rollback",
        "configDigest": "sha256:old",
    }
    before_spec = {
        "remoteWriteSettings": {"label": {"cluster": "old"}},
        "remoteWrite": [{"url": "http://old.invalid"}],
    }
    desired_spec = {"remoteWriteSettings": {}, "remoteWrite": [{"url": "http://new.invalid"}]}
    child = {
        "metadata": metadata,
        "spec": {
            "suspend": False,
            "chartRef": {"kind": "HelmChart", "name": "chart"},
            "values": {"vmagent": {"spec": desired_spec}},
        },
        "status": {
            "observedGeneration": 1,
            "lastAttemptedReleaseAction": "upgrade",
            "lastAttemptedConfigDigest": "sha256:desired",
            "lastAttemptedRevision": "1.0.0",
            "history": [rollback, failed],
            "conditions": [
                {"type": "Stalled", "status": "True", "reason": "RetriesExceeded"},
                {"type": "Remediated", "status": "True", "reason": "RollbackSucceeded"},
                {
                    "type": "Released",
                    "status": "False",
                    "reason": "UpgradeFailed",
                    "message": 'VMAgent spec.remoteWriteSettings invalid "null"',
                },
            ],
        },
    }
    agent = {
        "kind": "VMAgent",
        "apiVersion": "operator.victoriametrics.com/v1beta1",
        "metadata": {
            **metadata,
            "uid": "agent-uid",
            "annotations": {
                "meta.helm.sh/release-name": "metrics",
                "meta.helm.sh/release-namespace": "flux-system",
            },
            "labels": {
                "app.kubernetes.io/managed-by": "Helm",
                "helm.toolkit.fluxcd.io/name": "metrics",
                "helm.toolkit.fluxcd.io/namespace": "flux-system",
            },
        },
        "spec": copy.deepcopy(before_spec),
    }
    source = {
        "metadata": {**metadata, "name": "chart", "uid": "chart-uid"},
        "spec": {"chart": "metrics", "version": "1.0.0"},
        "status": {
            "observedGeneration": 1,
            "artifact": {"digest": "sha256:chart"},
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }
    row = {
        "namespace": "flux-system",
        "releaseName": "metrics",
        "upstreamReleaseName": "soperator-fluxcd-vm-stack",
        "sourceKind": "HelmChart",
        "sourceName": "chart",
        "revision": "sha256:chart",
    }
    graph = {"releases": [row]}
    admission = {"desiredGraphSha256": soperator_sha256(graph)}
    objects = {
        (HR, "flux-system", "metrics"): child,
        (repair.AGENT, "flux-system", "metrics"): agent,
        ("helmchart", "flux-system", "chart"): source,
    }
    patches, checkpoint = [], {}

    def run(args, *, input_text=None):
        ns, verb, kind, name = args[2:6]
        obj = objects.get((kind, ns, name))
        if verb == "get":
            return SimpleNamespace(stdout=json.dumps(obj) if obj else "")
        assert verb == "patch"
        target = copy.deepcopy(obj)
        operations = json.loads(input_text)
        for patch in operations:
            keys = patch["path"].strip("/").split("/")
            cursor = target
            for key in keys[:-1]:
                cursor = cursor[key]
            if patch["op"] == "test":
                assert cursor[keys[-1]] == patch["value"]
            else:
                cursor[keys[-1]] = copy.deepcopy(patch["value"])
        if "--dry-run=server" not in args:
            patches.append((kind, copy.deepcopy(operations)))
            target["metadata"]["generation"] += 1
            target["metadata"]["resourceVersion"] = str(
                int(target["metadata"]["resourceVersion"]) + 1
            )
            if kind == HR:
                target["status"]["observedGeneration"] = target["metadata"]["generation"]
            obj.clear()
            obj.update(target)
        return SimpleNamespace(stdout=json.dumps(target))

    def persist(value):
        checkpoint.clear()
        checkpoint.update(copy.deepcopy(value))

    op = NativeGraphTransition(
        {
            "schema": SCHEMA,
            "admission": admission,
            "admissionSha256": soperator_sha256(admission),
            "phase": "verified-absent",
        },
        run=run,
        persist=persist,
        authority=lambda: None,
    )
    monkeypatch.setattr(repair, "publication", lambda operation, resource: {"publication": "exact"})
    monkeypatch.setattr(repair, "verify_desired_agent", lambda *args: None)
    manifests = {
        2: [{**agent, "spec": copy.deepcopy(desired_spec)}],
        3: [{**agent, "spec": copy.deepcopy(before_spec)}],
    }

    def helm_get(operation, resource, verb, revision, **kwargs):
        if verb == "metadata":
            release = failed if revision == 2 else rollback
            return {**release, "revision": revision, "applyMethod": "ssa"}
        if verb == "manifest":
            return copy.deepcopy(manifests[revision])
        assert verb == "values"
        return copy.deepcopy(child["spec"]["values"])

    monkeypatch.setattr(repair, "helm_get", helm_get)

    def complete():
        agent["spec"] = copy.deepcopy(desired_spec)
        child["status"]["lastHandledResetAt"] = op.state[repair.STATE]["token"]
        child["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
        child["status"]["observedGeneration"] = child["metadata"]["generation"]

    return SimpleNamespace(
        op=op,
        child=child,
        agent=agent,
        source=source,
        graph=graph,
        row=row,
        objects=objects,
        patches=patches,
        checkpoint=checkpoint,
        complete=complete,
        manifests=manifests,
    )


def start(r):
    assert repair.recover_stage(r.op, r.graph, r.child)
    assert r.child["spec"]["suspend"]
    assert repair.recover_stage(r.op, r.graph, r.child)
    assert r.op.state[repair.STATE]["phase"] == "reset-requested"


def test_only_field_is_materialized_then_exact_retry_converges(recovery):
    r = recovery
    start(r)
    assert r.agent["spec"] == {
        "remoteWriteSettings": {},
        "remoteWrite": [{"url": "http://old.invalid"}],
    }
    patches = [
        p
        for kind, patches in r.patches
        if kind == repair.AGENT
        for p in patches
        if p["op"] != "test"
    ]
    assert patches == [{"op": "replace", "path": "/spec/remoteWriteSettings", "value": {}}]
    token = r.op.state[repair.STATE]["token"]
    assert r.child["metadata"]["annotations"][repair.RESET] == token
    assert repair.FORCE not in r.child["metadata"]["annotations"]
    assert repair.recover_stage(r.op, r.graph, r.child)
    r.complete()
    assert not repair.recover_stage(r.op, r.graph, r.child)
    assert r.op.state[repair.STATE]["phase"] == "converged"
    count = len(r.patches)
    assert not repair.recover_stage(r.op, r.graph, r.child)
    assert len(r.patches) == count


@pytest.mark.parametrize(
    "boundary", ["intent-recorded", "materialized", "reset-requested", "agent-cas", "reset-cas"]
)
def test_interrupted_recovery_never_mints_second_retry(recovery, boundary):
    r = recovery
    persist, run = r.op.persist, r.op.run
    interrupted = False

    def save(value):
        nonlocal interrupted
        persist(value)
        if value.get(repair.STATE, {}).get("phase") == boundary and not interrupted:
            interrupted = True
            raise RuntimeError("interrupted save")

    def interrupted_run(args, **kwargs):
        nonlocal interrupted
        result = run(args, **kwargs)
        if (
            "patch" in args
            and "--dry-run=server" not in args
            and not interrupted
            and (
                (boundary == "agent-cas" and repair.AGENT in args)
                or (boundary == "reset-cas" and repair.RESET in kwargs.get("input_text", ""))
            )
        ):
            interrupted = True
            raise RuntimeError("lost response")
        return result

    r.op.persist, r.op.run = save, interrupted_run
    with pytest.raises(RuntimeError):
        for _ in range(3):
            repair.recover_stage(r.op, r.graph, r.child)
    r.op.state = copy.deepcopy(r.checkpoint)
    r.op.persist, r.op.run = persist, run
    for _ in range(3):
        repair.recover_stage(r.op, r.graph, r.child)
        if r.child["metadata"].get("annotations", {}).get(repair.RESET):
            break
    r.complete()
    assert not repair.recover_stage(r.op, r.graph, r.child)
    assert sum(any(p["path"] == "/metadata/annotations" for p in ps) for _, ps in r.patches) == 1
    assert sum(kind == repair.AGENT for kind, _ in r.patches) == 1


@pytest.mark.parametrize("drift", ["uid", "owner", "spec", "source", "values", "child", "controls"])
def test_pending_reset_refuses_drift_before_reopening(recovery, drift):
    r = recovery
    start(r)
    r.child["spec"]["suspend"] = True
    if drift == "uid":
        r.agent["metadata"]["uid"] = "replacement"
    elif drift == "owner":
        r.agent["metadata"]["annotations"]["meta.helm.sh/release-name"] = "foreign"
    elif drift == "spec":
        r.agent["spec"]["extra"] = True
    elif drift == "source":
        r.source["status"]["artifact"]["digest"] = "sha256:foreign"
    elif drift == "values":
        r.child["spec"]["values"]["extra"] = True
    elif drift == "child":
        r.child["metadata"]["uid"] = "replacement"
    elif drift == "controls":
        r.child["metadata"]["annotations"][repair.RESET] = "foreign"
    count = len(r.patches)
    with pytest.raises(SoperatorSafetyPauseError):
        repair.recover_stage(r.op, r.graph, r.child)
    assert len(r.patches) == count


def test_second_failed_attempt_is_terminal(recovery):
    r = recovery
    start(r)
    r.child["status"]["lastHandledResetAt"] = r.op.state[repair.STATE]["token"]
    count = len(r.patches)
    with pytest.raises(SoperatorSafetyPauseError, match="no second reset"):
        repair.recover_stage(r.op, r.graph, r.child)
    assert len(r.patches) == count


@pytest.mark.parametrize(
    "drift",
    [
        "generation",
        "rollback",
        "failed-revision",
        "chart",
        "manifest",
        "preimage",
        "uid-owner",
        "different-error",
        "graph",
    ],
)
def test_unqualified_frontier_does_not_mutate(recovery, drift):
    r = recovery
    if drift == "generation":
        r.child["status"]["observedGeneration"] = 0
    elif drift == "rollback":
        r.child["status"]["history"][0]["action"] = "upgrade"
    elif drift == "failed-revision":
        r.child["status"]["history"][1]["version"] = 1
    elif drift == "chart":
        r.source["status"]["artifact"]["digest"] = "sha256:foreign"
    elif drift == "manifest":
        r.manifests[2][0]["spec"]["remoteWriteSettings"] = {"other": True}
    elif drift == "preimage":
        r.agent["spec"]["foreign"] = True
    elif drift == "uid-owner":
        r.agent["metadata"]["annotations"]["meta.helm.sh/release-name"] = "foreign"
    elif drift == "different-error":
        r.child["status"]["conditions"][-1]["message"] = "unrelated upgrade failure"
    elif drift == "graph":
        r.graph["foreign"] = True
    with pytest.raises(SoperatorSafetyPauseError):
        repair.recover_stage(r.op, r.graph, r.child)
    assert not r.patches


def test_convergence_checks_complete_target_spec(recovery):
    r = recovery
    start(r)
    r.complete()
    r.agent["spec"]["remoteWrite"] = [{"url": "http://wrong.invalid"}]
    with pytest.raises(SoperatorSafetyPauseError, match="VMAgent changed"):
        repair.recover_stage(r.op, r.graph, r.child)


def test_values_references_merge_and_capture_identity(recovery):
    r = recovery
    r.child["spec"]["valuesFrom"] = [
        {"kind": "ConfigMap", "name": "values"},
        {"kind": "ConfigMap", "name": "missing", "optional": True},
    ]
    r.objects[("configmap", "flux-system", "values")] = {
        "metadata": {"uid": "values-uid"},
        "data": {"values.yaml": "shared: true\nvmagent:\n  spec:\n    extra: kept\n"},
    }
    values, sources = repair.effective_values(r.op, r.child)
    assert values["shared"] is True
    assert values["vmagent"]["spec"]["extra"] == "kept"
    assert values["vmagent"]["spec"]["remoteWriteSettings"] == {}
    assert sources[0]["uid"] == "values-uid" and sources[1]["absent"]
    assert "kept" not in json.dumps(sources)


@pytest.mark.parametrize(
    "ref",
    [
        {"kind": "Secret", "name": "values"},
        {"kind": "ConfigMap", "name": "values", "targetPath": "x"},
    ],
)
def test_unsupported_values_reference_stops_before_mutation(recovery, ref):
    r = recovery
    r.child["spec"]["valuesFrom"] = [ref]
    with pytest.raises(SoperatorSafetyPauseError, match="unsupported values"):
        repair.recover_stage(r.op, r.graph, r.child)
    assert not r.patches


@pytest.mark.parametrize("drift", ["source", "values", "agent", "parent-child"])
def test_drift_during_materialized_checkpoint_never_resets(recovery, drift):
    r = recovery
    persist = r.op.persist

    def save(value):
        persist(value)
        if value.get(repair.STATE, {}).get("phase") == "materialized":
            if drift == "source":
                r.source["status"]["artifact"]["digest"] = "sha256:foreign"
            elif drift == "values":
                r.child["spec"]["values"]["extra"] = True
            elif drift == "agent":
                r.agent["spec"]["extra"] = True
            else:
                r.child["spec"]["suspend"] = False

    r.op.persist = save
    assert repair.recover_stage(r.op, r.graph, r.child)
    with pytest.raises(SoperatorSafetyPauseError):
        repair.recover_stage(r.op, r.graph, r.child)
    assert repair.RESET not in r.child["metadata"].get("annotations", {})


@pytest.mark.parametrize("drift", [None, "package", "render", "post-renderer"])
def test_mutation_content_is_derived_from_frozen_chart(recovery, monkeypatch, tmp_path, drift):
    r = recovery
    payload = b"fixture chart content"
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    charts = tmp_path / "charts"
    charts.mkdir()
    package = charts / (digest.removeprefix("sha256:") + ".tgz")
    package.write_bytes(payload if drift != "package" else b"changed")
    monkeypatch.setattr(repair, "default_soperator_source_cache_root", lambda: tmp_path)
    desired = copy.deepcopy(r.manifests[2][0])
    rendered = copy.deepcopy(desired)
    if drift == "render":
        rendered["spec"]["remoteWriteSettings"] = {"foreign": True}
    if drift == "post-renderer":
        r.child["spec"]["postRenderers"] = [{"kustomize": {"patches": []}}]

    def render(args, *, input_text):
        assert args[:4] == ["helm", "template", "metrics", str(package)]
        assert json.loads(input_text) == r.child["spec"]["values"]
        return SimpleNamespace(stdout=json.dumps(rendered))

    r.op.run = render
    if drift:
        with pytest.raises(SoperatorSafetyPauseError):
            VERIFY_DESIRED_AGENT(
                r.op, r.child, {**r.row, "revision": digest}, r.child["spec"]["values"], desired
            )
    else:
        VERIFY_DESIRED_AGENT(
            r.op, r.child, {**r.row, "revision": digest}, r.child["spec"]["values"], desired
        )
    assert not r.patches
