from __future__ import annotations

import copy
import json
import subprocess
from types import SimpleNamespace

import pytest

from nebius_cxcli import soperator_status_collect as collect
from nebius_cxcli.soperator_status_health import project_status_health
from status_health_fakes import add_flux_graph, add_node_configurator, healthy_snapshot


def transport(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failed: str = "",
    duplicate: bool = False,
    nodes: str = "NodeName=worker-0 State=IDLE\nNodeName=worker-1 State=ALLOCATED",
    source: dict | None = None,
    missing_crd: str = "",
    extra_crds: tuple[str, ...] = (),
):
    source = healthy_snapshot() if source is None else source
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert not {"apply", "patch", "delete", "create", "sbatch", "srun"}.intersection(argv)
        if "exec" in argv:
            assert argv[argv.index("-c") + 1] == "sshd"
            assert kwargs["timeout"] == 10
            if failed == "slurm":
                raise subprocess.TimeoutExpired(argv, 10)
            return SimpleNamespace(
                returncode=0,
                stdout="Slurmctld(primary) at controller-0 is UP" if argv[-1] == "ping" else nodes,
            )
        assert kwargs["timeout"] == 30
        if argv[0] == "helm":
            return SimpleNamespace(returncode=0, stdout=json.dumps([source["status_release"]]))
        kind = argv[argv.index("get") + 1]
        assert (
            "secret" not in kind and "configmap" not in kind and kind not in {"pv", "pvc", "nodes"}
        )
        if failed and failed in kind:
            raise subprocess.CalledProcessError(1, argv, stderr="sensitive provider output")
        if kind == "namespace":
            payload = {"metadata": {"uid": "cluster-uid-a"}}
        elif kind == "crd":
            payload = {
                "items": [
                    {"metadata": {"name": r}}
                    for r in (
                        *collect._CUSTOM_RESOURCES,
                        "statefulsets.apps.kruise.io",
                        *(
                            ["helmreleases.helm.toolkit.fluxcd.io"]
                            if "flux_releases" in source
                            else []
                        ),
                    )
                    if r != missing_crd
                ]
                + [{"metadata": {"name": name}} for name in extra_crds]
            }
        elif kind == "helmreleases.helm.toolkit.fluxcd.io":
            payload = {"items": source["flux_releases"]}
        elif kind.startswith("deployments"):
            ns = argv[argv.index("-n") + 1]
            payload = {
                "items": [w for w in source["workloads"] if w["metadata"]["namespace"] == ns]
            }
        else:
            resource_kind = (
                "SlurmCluster"
                if kind.startswith("slurmclusters")
                else collect._CUSTOM_RESOURCES[kind]
            )
            items = [r for r in source["soperator_resources"] if r["kind"] == resource_kind]
            if duplicate and resource_kind == "SlurmCluster":
                other = copy.deepcopy(items[0])
                other["metadata"]["uid"] = "foreign"
                items.append(other)
            payload = {"items": items}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    monkeypatch.setattr(collect.kubernetes_process, "run", run)
    return calls


def test_scoped_single_pass_collection_and_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = transport(monkeypatch)
    identity = collect.read_status_identity(kube_context="ctx")
    progress = []
    snapshot = collect.collect_status_snapshot(
        kube_context="ctx", identity=identity, progress=progress.append
    )
    assert project_status_health(snapshot).overall == "Healthy"
    assert progress[-2:] == ["Checking Slurm controller responses", "Reading Slurm worker states"]
    assert len([a for a, _ in calls if "exec" in a]) == 2
    assert len({tuple(a) for a, _ in calls}) == len(calls)


@pytest.mark.parametrize(
    "resource", ["helmreleases.helm.toolkit.fluxcd.io", "statefulsets.apps.kruise.io"]
)
@pytest.mark.parametrize("name_pattern", ["{}", "prefix-{}", "{}.example.test", "{}/v1", ""])
def test_optional_resource_discovery_requires_exact_crd_name(
    monkeypatch: pytest.MonkeyPatch, resource: str, name_pattern: str
) -> None:
    source = healthy_snapshot()
    add_flux_graph(source, configurator=False)
    calls = transport(
        monkeypatch,
        source=source,
        missing_crd=resource,
        extra_crds=(name_pattern.format(resource),),
    )
    collect.collect_status_snapshot(
        kube_context="ctx", identity={"cluster_identity": {"kubernetes_uid": "uid"}}
    )
    queried_kinds = {
        kind
        for argv, _ in calls
        if argv[0] == "kubectl" and "get" in argv
        for kind in argv[argv.index("get") + 1].split(",")
    }
    assert (resource in queried_kinds) == (name_pattern == "{}")
    if resource == "helmreleases.helm.toolkit.fluxcd.io":
        assert any(argv[0] == "helm" for argv, _ in calls) == (name_pattern != "{}")


@pytest.mark.parametrize("failed", ["nodesets.", "deployments", "slurm"])
def test_partial_failure_is_sanitized_and_retains_observations(
    monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    transport(monkeypatch, failed=failed)
    snapshot = collect.collect_status_snapshot(
        kube_context="ctx", identity={"cluster_identity": {"kubernetes_uid": "uid"}}
    )
    report = project_status_health(snapshot)
    assert report.overall != "Healthy"
    assert snapshot["status_issues"]
    assert "sensitive provider output" not in str(snapshot)
    assert snapshot["status_release"]["status"] == "deployed"


def test_identity_conflict_prevents_runtime_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = transport(monkeypatch, duplicate=True)
    with pytest.raises(RuntimeError, match="ambiguous"):
        collect.collect_status_snapshot(
            kube_context="ctx", identity={"cluster_identity": {"kubernetes_uid": "uid"}}
        )
    assert not any("exec" in a for a, _ in calls)


@pytest.mark.parametrize(
    "nodes", ["", "broken", "NodeName=worker-0 State=IDLE\nNodeName=worker-0 State=IDLE"]
)
def test_invalid_node_query_never_becomes_empty_healthy_inventory(
    monkeypatch: pytest.MonkeyPatch, nodes: str
) -> None:
    transport(monkeypatch, nodes=nodes)
    snapshot = collect.collect_status_snapshot(
        kube_context="ctx", identity={"cluster_identity": {"kubernetes_uid": "uid"}}
    )
    assert snapshot["slurm_nodes"]["state"] == "failed"
    assert project_status_health(snapshot).overall == "Unknown"


def test_identity_query_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    transport(monkeypatch, failed="namespace")
    with pytest.raises(RuntimeError, match="could not verify") as error:
        collect.read_status_identity(kube_context="ctx")
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize(
    "mode",
    [
        "healthy",
        "missing",
        "duplicate",
        "foreign_namespace",
        "storage_annotation",
        "foreign_release",
        "failed_read",
        "missing_crd",
        "optional_absent",
    ],
)
def test_flux_node_configurator_and_operator_use_target_ownership(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    source = healthy_snapshot()
    add_flux_graph(source, configurator=mode != "optional_absent")
    config = add_node_configurator(source)
    if mode in {"missing", "optional_absent"}:
        source["soperator_resources"].remove(config)
    elif mode == "duplicate":
        other = copy.deepcopy(config)
        other["metadata"].update(name="another", uid="another")
        source["soperator_resources"].append(other)
    elif mode == "foreign_namespace":
        config["metadata"]["namespace"] = "foreign"
    elif mode == "storage_annotation":
        config["metadata"]["annotations"]["meta.helm.sh/release-namespace"] = "flux-system"
    elif mode == "foreign_release":
        config["metadata"]["annotations"]["meta.helm.sh/release-name"] = "foreign"
    calls = transport(
        monkeypatch,
        source=source,
        failed="nodeconfigurators." if mode == "failed_read" else "",
        missing_crd="nodeconfigurators.slurm.nebius.ai" if mode == "missing_crd" else "",
    )
    report = project_status_health(
        collect.collect_status_snapshot(
            kube_context="ctx", identity={"cluster_identity": {"kubernetes_uid": "uid"}}
        )
    )
    operator = next(r for r in report.components if r.component == "Soperator operator")
    assert operator.state == "Healthy"
    assert not any(argv[0] == "helm" for argv, _ in calls)
    rows = [r for r in report.components if r.component.startswith("Node configuration")]
    if mode == "optional_absent":
        assert not rows and report.overall == "Healthy"
    elif mode == "healthy":
        assert len(rows) == 1 and rows[0].state == "Healthy"
        assert rows[0].ready == rows[0].expected == 5
    else:
        assert len(rows) == 1 and rows[0].state == "Unknown"
        assert report.overall == "Unknown"


@pytest.mark.parametrize(
    ("failed", "missing", "expected"),
    [(False, False, "collected"), (True, False, "unavailable"), (False, True, "not_installed")],
)
def test_history_availability_is_separate_from_current_health(
    monkeypatch: pytest.MonkeyPatch, failed: bool, missing: bool, expected: str
) -> None:
    transport(
        monkeypatch,
        failed="activechecks." if failed else "",
        missing_crd="activechecks.slurm.nebius.ai" if missing else "",
    )
    snapshot = collect.collect_status_snapshot(
        kube_context="ctx", identity={"cluster_identity": {"kubernetes_uid": "uid"}}
    )
    report = project_status_health(snapshot)
    assert report.history.state == expected
    assert not report.history.records and not report.issues
    assert report.overall == "Healthy"
    assert "sensitive" not in str(report)
