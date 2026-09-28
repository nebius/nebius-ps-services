import copy
import json
from types import SimpleNamespace

import pytest

from nebius_cxcli.soperator_failures import SoperatorSafetyPauseError
from nebius_cxcli.soperator_flux_graph import SOPERATOR_GRAPH_LABEL, SOPERATOR_GRAPH_LABEL_VALUE
from nebius_cxcli.soperator_graph_transition import (
    HR,
    SCHEMA,
    NativeGraphTransition,
    capture_transition,
    classify_graph,
    identity,
    preflight_transition,
    qualify_inventory,
    witness,
)
from nebius_cxcli.soperator_observability_scope import OWNED_WRITER, WRITER
from nebius_cxcli.soperator_operation import soperator_sha256


def resource(name, *, namespace="flux-system", uid=None):
    return {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "uid": uid or name + "-uid",
            "resourceVersion": "1",
            "generation": 1,
            "labels": {
                SOPERATOR_GRAPH_LABEL: SOPERATOR_GRAPH_LABEL_VALUE,
                "helm.toolkit.fluxcd.io/name": "parent",
                "helm.toolkit.fluxcd.io/namespace": "flux-system",
            },
            "annotations": {
                "meta.helm.sh/release-name": "parent",
                "meta.helm.sh/release-namespace": "flux-system",
            },
        },
        "spec": {"suspend": False, "values": {"source": True}},
    }


def test_classifies_and_rejects_unsupported_removals_before_writes():
    parent, kept, retiring = resource("parent"), resource("kept"), resource(OWNED_WRITER)
    desired = {
        "releases": [
            {"namespace": "flux-system", "releaseName": "kept", "dependencies": []},
            {"namespace": "flux-system", "releaseName": "added", "dependencies": ["kept"]},
        ]
    }
    assert classify_graph([parent, kept, retiring], desired, parent) == {
        "retained": [("flux-system", "kept")],
        "added": [("flux-system", "added")],
        "retiring": [("flux-system", OWNED_WRITER)],
    }
    with pytest.raises(SoperatorSafetyPauseError, match="unqualified"):
        classify_graph([parent, kept, resource("unreviewed")], desired, parent)
    retiring["metadata"]["annotations"]["meta.helm.sh/release-namespace"] = "other"
    with pytest.raises(SoperatorSafetyPauseError, match="foreign"):
        classify_graph([parent, kept, retiring], desired, parent)
    desired["releases"][0]["dependencies"] = [OWNED_WRITER]
    with pytest.raises(SoperatorSafetyPauseError, match="dependency"):
        classify_graph([parent], desired, parent)


@pytest.mark.parametrize(
    "kind,annotations",
    [
        ("Secret", {}),
        ("PersistentVolumeClaim", {}),
        ("Deployment", {"helm.sh/resource-policy": "keep"}),
        ("Deployment", {"helm.sh/hook": "post-delete"}),
    ],
)
def test_uninstall_inventory_rejects_persistent_or_hook_resources(kind, annotations):
    with pytest.raises(SoperatorSafetyPauseError, match="not qualified"):
        qualify_inventory(
            [
                {
                    "kind": kind,
                    "metadata": {
                        "name": WRITER,
                        "namespace": "monitoring",
                        "annotations": annotations,
                    },
                    "spec": {"selector": {"matchLabels": {"writer": "test"}}},
                }
            ]
        )


class Cluster:
    def __init__(self):
        self.parent, self.child = resource("parent"), resource(OWNED_WRITER)
        self.resources = {(HR, *identity(row)): row for row in (self.parent, self.child)}
        self.deployment = resource(WRITER, namespace="monitoring")
        self.deployment["kind"] = "Deployment"
        self.resources[("Deployment", "monitoring", WRITER)] = self.deployment
        self.pods = [{"metadata": {"name": "writer-pod", "uid": "pod-uid"}}]
        self.events = []
        self.delay_cleanup = False
        self.fail_after_parent = False

    def run(self, args, *, input_text=None):
        if args[:3] == ["helm", "get", "manifest"]:
            return SimpleNamespace(stdout="")
        namespace, verb, kind = args[2:5]
        name = args[5] if kind != "pods" else ""
        if verb == "get":
            if kind == "pods":
                return SimpleNamespace(stdout=json.dumps({"items": self.pods}))
            row = self.resources.get((kind, namespace, name))
            return SimpleNamespace(stdout=json.dumps(row) if row else "")
        assert verb == "patch", "Retirement must never directly delete resources"
        row = self.resources[(kind, namespace, name)]
        assert args[-2:] == ["--patch-file", "/dev/stdin"]
        patches = json.loads(input_text)
        assert patches[:3] == [
            {"op": "test", "path": "/metadata/uid", "value": row["metadata"]["uid"]},
            {
                "op": "test",
                "path": "/metadata/resourceVersion",
                "value": row["metadata"]["resourceVersion"],
            },
            {"op": "test", "path": "/spec", "value": row["spec"]},
        ]
        if "--dry-run=server" in args:
            return SimpleNamespace(stdout=json.dumps({**row, "spec": patches[3]["value"]}))
        row["spec"] = patches[3]["value"]
        row["metadata"]["resourceVersion"] = str(int(row["metadata"]["resourceVersion"]) + 1)
        row["metadata"]["generation"] += 1
        self.events.append((name, row["spec"]["suspend"]))
        if name == "parent" and row["spec"].get("values") == {"desired": True}:
            assert self.child["spec"]["suspend"] is False, (
                "Suspended deletion would orphan the workload"
            )
            self.resources.pop((HR, "flux-system", OWNED_WRITER), None)
            if not self.delay_cleanup:
                self.resources.pop(("Deployment", "monitoring", WRITER), None)
                self.pods = []
            row["status"] = {
                "history": [{"status": "deployed", "version": 2}],
                "observedGeneration": row["metadata"]["generation"],
                "conditions": [{"type": "Ready", "status": "True"}],
            }
            if self.fail_after_parent:
                self.fail_after_parent = False
                raise RuntimeError("lost parent response")
        return SimpleNamespace(stdout="{}")


def transition(cluster, persist=lambda _: None):
    admission = {
        "parent": witness(cluster.parent),
        "child": witness(cluster.child),
        "inventory": [
            {
                "kind": "Deployment",
                "namespace": "monitoring",
                "name": WRITER,
                "uid": cluster.deployment["metadata"]["uid"],
                "selector": {"writer": "test"},
            }
        ],
    }
    state = {
        "schema": SCHEMA,
        "admission": admission,
        "admissionSha256": soperator_sha256(admission),
        "phase": "intent-recorded",
    }
    return NativeGraphTransition(state, run=cluster.run, persist=persist, authority=lambda: None)


def desired(cluster):
    return {**cluster.parent, "spec": {"suspend": False, "values": {"desired": True}}}


def test_retirement_unsuspends_child_before_parent_prune_and_waits_for_pods():
    cluster = Cluster()
    states = []
    operation = transition(cluster, lambda value: states.append(value))
    target = desired(cluster)
    operation.fence()
    operation.publish(target)
    operation.wait_absent(timeout=0, interval=0)
    assert cluster.events == [
        ("parent", True),
        (OWNED_WRITER, True),
        (OWNED_WRITER, False),
        ("parent", False),
    ]
    assert [state["phase"] for state in states] == [
        "intent-recorded",
        "uninstall-enabled",
        "deletion-pending",
        "cleanup-pending",
        "cleanup-pending",  # Seal the reconciled Helm revision before completion.
        "verified-absent",
    ]


@pytest.mark.parametrize(
    "boundary", ["intent-recorded", "uninstall-enabled", "deletion-pending", "parent-response"]
)
def test_replay_after_each_publication_boundary_keeps_frozen_identity(boundary):
    cluster = Cluster()
    checkpoint = {}
    failed = False

    def persist(value):
        nonlocal failed
        checkpoint.clear()
        checkpoint.update(copy.deepcopy(value))
        if value["phase"] == boundary and not failed:
            failed = True
            raise RuntimeError("checkpoint interruption")

    operation = transition(cluster, persist)
    target = desired(cluster)
    operation.fence()
    cluster.fail_after_parent = boundary == "parent-response"
    with pytest.raises(RuntimeError):
        operation.publish(target)
    replay = NativeGraphTransition(
        checkpoint, run=cluster.run, persist=lambda value: None, authority=lambda: None
    )
    replay.fence()
    replay.publish(target)
    replay.wait_absent(timeout=0, interval=0)
    assert replay.state["phase"] == "verified-absent"
    assert replay.admission == operation.admission


def test_helmrelease_absence_does_not_prove_workload_cleanup():
    cluster = Cluster()
    operation = transition(cluster)
    target = desired(cluster)
    operation.fence()
    cluster.delay_cleanup = True
    operation.publish(target)
    with pytest.raises(SoperatorSafetyPauseError, match="not completed"):
        operation.wait_absent(timeout=0, interval=0)
    assert operation.state["phase"] == "cleanup-pending"
    cluster.resources.pop(("Deployment", "monitoring", WRITER))
    with pytest.raises(SoperatorSafetyPauseError, match="not completed"):
        operation.wait_absent(timeout=0, interval=0)
    cluster.pods = []
    operation.wait_absent(timeout=0, interval=0)


def test_parent_replacement_is_rejected_before_replay_writes():
    cluster = Cluster()
    operation = transition(cluster)
    target = desired(cluster)
    operation.fence()
    operation.publish(target)
    cluster.parent["metadata"]["uid"] = "foreign"
    before = list(cluster.events)
    with pytest.raises(SoperatorSafetyPauseError, match="replaced"):
        operation.fence()
    assert cluster.events == before


def test_reappearing_retired_child_is_not_hidden_by_completed_checkpoint():
    cluster = Cluster()
    operation = transition(cluster)
    operation.state["phase"] = "verified-absent"
    with pytest.raises(SoperatorSafetyPauseError, match="reappeared"):
        operation.frontier([cluster.parent, cluster.child])


def test_api_failure_is_not_treated_as_absence():
    cluster = Cluster()
    operation = transition(cluster)

    def failed(_args):
        raise RuntimeError("API unavailable")

    operation.run = failed
    with pytest.raises(RuntimeError, match="API unavailable"):
        operation.get(HR, "flux-system", OWNED_WRITER)


def test_campaign_preview_uses_desired_graph_and_rejects_before_maintenance():
    parent = resource("parent")
    parent["metadata"]["labels"] = {}
    snapshot = SimpleNamespace(release_graph=[])
    live = [parent, resource("kept"), resource("removed")]
    events = []

    def observe(_):
        events.append("observe")
        return SimpleNamespace(stdout=json.dumps({"items": live}))

    with pytest.raises(SoperatorSafetyPauseError, match="unqualified"):
        preflight_transition(
            snapshot,
            run=observe,
            desired_graph={
                "releases": [
                    {"namespace": "flux-system", "releaseName": "kept", "dependencies": []},
                ]
            },
        )
        events.append("maintenance")
    assert events == ["observe"]


@pytest.fixture
def pinned_capture():
    import yaml

    parent, child = resource("parent"), resource(OWNED_WRITER)
    parent["status"] = {"history": [{"status": "deployed", "version": 3}]}
    child["status"] = {"history": [{"status": "deployed", "version": 2}]}
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"namespace": "monitoring", "name": WRITER},
        "spec": {"selector": {"matchLabels": {"app": WRITER}}, "replicas": 1},
    }
    child["spec"] = {
        "suspend": False,
        "releaseName": "tsa-token-writer",
        "targetNamespace": "monitoring",
        "chartRef": {"kind": "HelmChart", "name": "raw-source"},
        "values": {"resources": [deployment]},
    }
    outer = copy.deepcopy(parent)
    outer["spec"]["values"] = {"observability": {"publicEndpointEnabled": False}}
    source = {
        "kind": "HelmChart",
        "metadata": {"namespace": "flux-system", "name": "raw-source"},
        "spec": {
            "chart": "raw",
            "version": "2.0.0",
            "sourceRef": {"kind": "HelmRepository", "name": "raw-repo"},
            "suspend": True,
        },
    }
    repo = {
        "kind": "HelmRepository",
        "metadata": {"namespace": "flux-system", "name": "raw-repo"},
        "spec": {"url": "https://charts.example.invalid"},
    }
    digest = "sha256:" + "a" * 64
    writer = {
        "releaseName": OWNED_WRITER,
        "upstreamReleaseName": WRITER,
        "namespace": "flux-system",
        "sourceKind": "HelmChart",
        "sourceName": "raw-source",
        "revision": digest,
        "dependencies": [],
        "stage": 0,
    }
    predecessor, desired_graph = {"releases": [writer]}, {"releases": []}
    live_source = copy.deepcopy(source)
    live_source["spec"].update(suspend=False, reconcileStrategy="ChartVersion")
    live_source["status"] = {"artifact": {"digest": digest}}
    live_deployment = copy.deepcopy(deployment)
    live_deployment["metadata"].update(
        uid="writer-deployment-uid",
        resourceVersion="1",
        annotations={
            "meta.helm.sh/release-name": "tsa-token-writer",
            "meta.helm.sh/release-namespace": "monitoring",
        },
    )
    events = []

    def run(args, *, input_text=None):
        events.append(args)
        if "replace" in args and "--dry-run=server" in args:
            return SimpleNamespace(stdout=input_text)
        if "--dry-run=server" in args:
            return SimpleNamespace(
                stdout=json.dumps({**child, "spec": json.loads(input_text)[3]["value"]})
            )
        if args[:3] == ["helm", "get", "manifest"]:
            if args[3] == "parent":
                assert args[-2:] == ["--revision", "3"]
                return SimpleNamespace(stdout=yaml.safe_dump(child))
            assert args[3] == "tsa-token-writer" and args[-2:] == ["--revision", "2"]
            return SimpleNamespace(stdout=yaml.safe_dump(deployment))
        objects = {"HelmChart": live_source, "HelmRepository": repo, "Deployment": live_deployment}
        return SimpleNamespace(stdout=json.dumps(objects[args[4]]))

    arguments = dict(
        resources=[parent, child],
        desired=desired_graph,
        outer=outer,
        target={"targetRef": "target"},
        run=run,
        predecessor_graph=predecessor,
        sources=[source, repo],
    )
    return arguments, live_source, live_deployment, events


def test_capture_qualifies_real_third_party_chart_kind_and_deployed_revisions(pinned_capture):
    arguments, _, _, events = pinned_capture
    state = capture_transition(**arguments)
    assert state["admission"]["childRevision"] == "2"
    assert state["admission"]["parentRevision"] == "3"
    assert state["admission"]["chartSha256"] == "sha256:" + "a" * 64
    assert not any(
        "delete" in args or "apply" in args or ("patch" in args and "--dry-run=server" not in args)
        for args in events
    )


@pytest.mark.parametrize("change", ["digest", "source", "foreign-workload", "graph"])
def test_capture_rejects_unqualified_chart_workload_or_graph(pinned_capture, change):
    arguments, source, deployment, _ = pinned_capture
    if change == "digest":
        source["status"]["artifact"]["digest"] = "sha256:" + "b" * 64
    elif change == "source":
        source["spec"]["version"] = "foreign"
    elif change == "foreign-workload":
        deployment["metadata"]["annotations"]["meta.helm.sh/release-namespace"] = "foreign"
    else:
        arguments["desired"]["releases"].append(
            {
                "releaseName": "unrelated",
                "upstreamReleaseName": "unrelated",
                "namespace": "flux-system",
                "dependencies": [],
            }
        )
    with pytest.raises(SoperatorSafetyPauseError):
        capture_transition(**arguments)


def test_campaign_replay_uses_progress_without_rebinding_admission():
    from nebius_cxcli.soperator_graph_transition import campaign_transition_checkpoint

    initial = transition(Cluster()).state
    progressed = {**initial, "phase": "cleanup-pending", "publishedSpecSha256": "sha256:target"}
    evidence = {
        "events": [{"action": "native-graph-admitted", "transition": initial}],
        "nativeGraphTransition": progressed,
    }
    assert campaign_transition_checkpoint(evidence) == progressed
    assert campaign_transition_checkpoint({"events": evidence["events"]}) == initial
    with pytest.raises(SoperatorSafetyPauseError, match="admission"):
        campaign_transition_checkpoint(
            {**evidence, "nativeGraphTransition": {**progressed, "admissionSha256": "changed"}}
        )


@pytest.mark.parametrize("status", ["restored", "recovery-required"])
def test_completed_scheduling_history_cannot_authorize_a_new_writer(pinned_capture, status):
    from nebius_cxcli.soperator_graph_transition import active_transition_checkpoint

    arguments, *_ = pinned_capture
    previous = capture_transition(**arguments)
    child = arguments["resources"][1]
    selected = active_transition_checkpoint({"status": status, "nativeGraphTransition": previous})
    original_run = arguments["run"]

    def run(args, **kwargs):
        if args[:1] == ["kubectl"] and args[3:5] == ["get", HR]:
            return SimpleNamespace(
                stdout=json.dumps(
                    next(
                        row for row in arguments["resources"] if row["metadata"]["name"] == args[5]
                    )
                )
            )
        return original_run(args, **kwargs)

    if selected is not None:
        replay = NativeGraphTransition(
            selected, run=run, persist=lambda _: None, authority=lambda: None
        )
        replay.verify_readonly()
    child["metadata"]["uid"] = "new-writer-uid"
    if status == "restored":
        assert selected is None
        fresh = capture_transition(**arguments)
        assert fresh["admission"]["child"]["uid"] == "new-writer-uid"
        assert fresh["admissionSha256"] != previous["admissionSha256"]
    else:
        with pytest.raises(SoperatorSafetyPauseError):
            replay.verify_readonly()


def test_stable_parent_publication_replay_retains_uid_and_spec_fences():
    cluster = Cluster()
    checkpoint = {}

    def persist(value):
        checkpoint.clear()
        checkpoint.update(copy.deepcopy(value))

    operation = transition(cluster, persist)
    target = desired(cluster)
    operation.fence()
    operation.publish(target)
    operation.wait_absent(timeout=0, interval=0)
    stable = {**target, "spec": {"suspend": True, "values": {"stable": True}}}
    operation.suspend_parent(True)
    operation.publish_parent(stable)
    replay = NativeGraphTransition(
        checkpoint, run=cluster.run, persist=persist, authority=lambda: None
    )
    replay.publish_parent(stable)
    replay.suspend_parent(False)
    replay.verify_readonly()
    cluster.parent["spec"]["values"] = {"foreign": True}
    count = len(cluster.events)
    with pytest.raises(SoperatorSafetyPauseError, match="publication changed"):
        replay.publish_parent(stable)
    assert len(cluster.events) == count


@pytest.mark.parametrize("version_suffix", ["matched", "foreign"])
def test_parent_version_digest_suffix_requires_full_frozen_source(version_suffix):
    from nebius_cxcli.soperator_graph_transition import qualify_parent_source

    digest = "sha256:" + "a" * 64
    source = {
        "kind": "OCIRepository",
        "metadata": {"name": "umbrella", "namespace": "flux-system"},
        "spec": {"url": "oci://registry.example.invalid/umbrella", "ref": {"digest": digest}},
        "status": {"artifact": {"revision": "4.1.11@" + digest}},
    }
    parent = resource("parent")
    parent["spec"]["chartRef"] = {"kind": "OCIRepository", "name": "umbrella"}
    parent["status"] = {
        "history": [
            {"chartVersion": "4.1.11+" + ("a" if version_suffix == "matched" else "b") * 12}
        ]
    }
    snapshot = SimpleNamespace(umbrella=SimpleNamespace(version="4.1.11", digest=digest))

    def verify():
        qualify_parent_source(
            snapshot,
            parent,
            sources=[source],
            run=lambda _: SimpleNamespace(stdout=json.dumps(source)),
        )

    if version_suffix == "matched":
        verify()
        source["spec"]["ref"]["digest"] = "sha256:" + "b" * 64
    with pytest.raises(SoperatorSafetyPauseError, match="same pinned"):
        verify()


@pytest.mark.parametrize("drift", ["status", "spec", "ownership"])
def test_patch_reobserves_only_status_races(drift):
    from nebius_cxcli.soperator_graph_transition import patch_spec

    cluster = Cluster()
    initial = copy.deepcopy(cluster.parent)
    attempts = []
    authorities = []

    def run(args, **kwargs):
        if "patch" in args:
            attempts.append(1)
            if len(attempts) == 1:
                cluster.parent["metadata"]["resourceVersion"] = "2"
                if drift == "spec":
                    cluster.parent["spec"]["values"]["foreign"] = True
                if drift == "ownership":
                    cluster.parent["metadata"]["labels"]["helm.toolkit.fluxcd.io/name"] = "foreign"
                raise RuntimeError("The request is invalid")
        return cluster.run(args, **kwargs)

    def execute():
        patch_spec(
            run,
            initial,
            {**initial["spec"], "suspend": True},
            authority=lambda: authorities.append(1),
        )

    if drift == "status":
        execute()
        assert cluster.parent["spec"]["suspend"] is True
        assert len(attempts) == len(authorities) == 2
    else:
        with pytest.raises(RuntimeError, match="request is invalid"):
            execute()
        assert len(attempts) == 1


def test_owned_child_relabel_cannot_reuse_admission():
    cluster = Cluster()
    operation = transition(cluster)
    cluster.child["metadata"]["annotations"]["meta.helm.sh/release-name"] = "other"
    with pytest.raises(SoperatorSafetyPauseError, match="frozen resource identity"):
        operation.fence()
