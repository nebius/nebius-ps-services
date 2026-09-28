"""Fresh observation replaces shared accepted-generation authority."""

import base64
import copy
import json
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import deployment_observed_source as source
from nebius_cxcli.deployment_state import DeploymentGeneration
from nebius_cxcli.soperator_adapter import (
    render_soperator_adapter_documents,
    soperator_adapter_state_from_documents,
)
from test_deployment_plan import config
from test_soperator_upstream_adapter import _RELEASE, _values


@pytest.mark.parametrize("outcome", ["present", "missing", "forbidden", "wrong-owner"])
def test_values_observation_distinguishes_missing_from_failed_or_unowned_reads(tmp_path, outcome):
    metadata = {"name": "values", "namespace": "flux-system", "labels": {"owner": "cluster"}}
    (tmp_path / "configmap-terraform-fluxcd-values.yaml").write_text(
        yaml.safe_dump({"metadata": metadata})
    )
    calls = []

    def read(namespace, args, **kwargs):
        calls.append((namespace, args, kwargs))
        assert kwargs["check"] is True
        assert kwargs["kube_context"] == "explicit-context"
        assert "--ignore-not-found=true" in args
        if outcome == "forbidden":
            raise RuntimeError("Forbidden")
        if outcome == "missing":
            return SimpleNamespace(stdout="")
        live = {"metadata": {**metadata, "uid": "uid"}, "data": {"values.yaml": "setting: 1"}}
        if outcome == "wrong-owner":
            live["metadata"]["labels"] = {"owner": "other"}
        return SimpleNamespace(stdout=json.dumps(live))

    cli = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context", _run_soperator_upgrade_kubectl=read
    )
    if outcome in {"forbidden", "wrong-owner"}:
        with pytest.raises(RuntimeError, match="Forbidden|ownership"):
            source.observe_upstream_values(
                cli, flux_dir=tmp_path, kube_env={"context": "explicit-context"}
            )
    else:
        observed = source.observe_upstream_values(
            cli, flux_dir=tmp_path, kube_env={"context": "explicit-context"}
        )
        assert observed == ({"setting": 1} if outcome == "present" else None)
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["unchanged", "active-slot", "capacity", "affinity", "missing"])
def test_same_release_repair_requires_live_physical_protection(tmp_path, monkeypatch, change):
    monkeypatch.setattr(
        "nebius_cxcli.flux_ops.stable_soperator_documents", lambda docs, releases: docs
    )
    values = _values()
    docs, _ = render_soperator_adapter_documents(values, release=_RELEASE)
    adapter = soperator_adapter_state_from_documents(docs)
    live_adapter = copy.deepcopy(adapter)
    if change == "active-slot":
        live_adapter["activeSlot"] = "different"
    graph = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "nebius-cxcli-soperator-release-graph", "namespace": "flux-system"},
        "data": {"graph.json": json.dumps({"releases": []})},
    }
    payload = config()
    payload["apps"]["charts"][0]["values"] = values
    generation = DeploymentGeneration(
        {
            "runtime_config": payload,
            "deploy": {"targets": [{"target_ref": "cluster", "flux_dir": "flux"}]},
        },
        {
            "flux/kustomization.yaml": base64.b64encode(b"resources: [adapter.yaml]\n").decode(),
            "flux/adapter.yaml": base64.b64encode(
                yaml.safe_dump_all([*docs, graph]).encode()
            ).decode(),
        },
    )
    inventory = {(doc["kind"], doc["metadata"]["name"]): copy.deepcopy(doc) for doc in docs}
    pv = next(row for row in inventory.values() if row["kind"] == "PersistentVolume")
    if change == "capacity":
        pv["spec"]["capacity"]["storage"] = "1Mi"
    if change == "affinity":
        pv["spec"]["nodeAffinity"] = {"required": {"nodeSelectorTerms": []}}
    read_count = []

    def read(namespace, args, **kwargs):
        assert kwargs["kube_context"] == "ctx"
        read_count.append(args)
        if change == "missing":
            return SimpleNamespace(stdout="")
        doc = copy.deepcopy(inventory[(args[1], args[2])])
        doc["metadata"]["uid"] = "uid-" + args[2]
        return SimpleNamespace(stdout=json.dumps(doc))

    cli = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _run_soperator_upgrade_kubectl=read,
        _rendered_soperator_adapter_state=lambda _: adapter,
    )
    monkeypatch.setattr(source, "observe_adapter", lambda *a, **kw: live_adapter)
    monkeypatch.setattr(source, "verify_worker_storage", lambda *a, **kw: None)
    consumers = []
    monkeypatch.setattr(
        "nebius_cxcli.deployment_jail_state.observe_jail_storage",
        lambda *a, **kw: consumers.append(kw),
    )
    kwargs = dict(
        generation=generation, flux_dir=tmp_path, target_ref="cluster", kube_env={"context": "ctx"}
    )
    if change == "unchanged":
        source.verify_reconcile_storage(cli, **kwargs)
        assert read_count and len(consumers) == 1
    else:
        with pytest.raises(
            RuntimeError, match="storage adapter|settings have not converged|resource is absent"
        ):
            source.verify_reconcile_storage(cli, **kwargs)
        assert not consumers


def test_observed_topology_retains_removed_group_and_zero_capacity():
    desired = config()
    component = desired["infra"]["components"][0]
    component["inputs"]["cluster"] = {"cluster_name": "authored-alias", "k8s_version": "1.32"}
    component["inputs"]["node_groups"] = {"new": {"node_count": 5}}
    cluster = {
        "name": "live-cluster",
        "parent_id": "project",
        "control_plane": {"subnet_id": "subnet", "version": "1.31"},
    }
    node = {
        "name": "old",
        "fixed_node_count": 0,
        "template": {"resources": {"platform": "cpu-e2", "preset": "2vcpu-8gb"}},
    }
    resources = [
        {
            "address": "module.cluster.cluster.main",
            "type": "nebius_mk8s_v1_cluster",
            "values": cluster,
        },
        {
            "address": "module.cluster.network.main",
            "type": "nebius_vpc_v1_network",
            "values": {"id": "network"},
        },
        {
            "address": 'module.cluster.groups["old"]',
            "type": "nebius_mk8s_v1_node_group",
            "index": "old",
            "values": node,
        },
    ]
    restored = source.project_observed_source(
        desired,
        target_ref="cluster",
        resources=resources,
        owned_modules={"cluster"},
        live_release="1.22.3",
        live_values={},
        terraform={},
    )
    actual = restored["infra"]["components"][0]["inputs"]
    assert set(actual["node_groups"]) == {"old"}
    assert actual["node_groups"]["old"]["node_count"] == 0
    assert actual["cluster"]["k8s_version"] == "1.31"
    assert component["inputs"]["cluster"]["k8s_version"] == "1.32"
    node["template"]["cloud_init_user_data"] = yaml.safe_dump(
        {
            "users": [
                {
                    "name": "user",
                    "ssh_authorized_keys": [],
                    "sudo": "ALL=(ALL) NOPASSWD:ALL",
                    "shell": "/bin/bash",
                }
            ],
            "runcmd": ["custom-command"],
        }
    )
    with pytest.raises(RuntimeError, match="canonical"):
        source.observed_node_group(node, resources)


@pytest.mark.parametrize("claim", ["slot-a", "slot-b"])
def test_worker_claim_mismatch_blocks_same_release_reconciliation(tmp_path, claim):
    expected = {"jail": {"persistentVolumeClaim": {"claimName": "slot-a"}}}
    live = {
        "metadata": {"name": "workers", "namespace": "soperator", "uid": "nodeset-uid"},
        "spec": {"slurmd": {"volumes": {"jail": {"persistentVolumeClaim": {"claimName": claim}}}}},
    }
    cli = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _rendered_soperator_upstream_values=lambda _: {
            "nodesets": {
                "overrideValues": {
                    "nodesets": [{"name": "workers", "slurmd": {"volumes": expected}}]
                }
            }
        },
        _soperator_upgrade_live_slurmcluster_namespaces=lambda **kw: ["soperator"],
        _run_soperator_upgrade_kubectl=lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps({"items": [live]})
        ),
    )
    if claim == "slot-a":
        source.verify_worker_storage(cli, flux_dir=tmp_path, kube_env={"context": "ctx"})
    else:
        with pytest.raises(RuntimeError, match="NodeSet storage differs"):
            source.verify_worker_storage(cli, flux_dir=tmp_path, kube_env={"context": "ctx"})


@pytest.mark.parametrize("case", ["repair", "coordinated", "missing-storage", "forbidden"])
def test_missing_values_repair_is_admitted_only_with_unchanged_live_protection(
    tmp_path, monkeypatch, case
):
    from nebius_cxcli import cli, deployment_cli

    payload = config()
    payload["infra"]["components"] = []
    if case == "coordinated":
        payload["apps"]["charts"][0]["version"] = "1.23.0"
    target = {"target_ref": "cluster", "ownership": "onboarded"}
    env = {
        cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "cluster-id",
        cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "ctx",
    }
    monkeypatch.setattr(cli, "_resolve_selected_deploy_targets", lambda *a, **kw: [target])
    monkeypatch.setattr(cli, "_generated_bundle_mk8s_module_index", lambda _: {})
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", lambda *a, **kw: env)
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **kw: "uid")
    monkeypatch.setattr(cli, "_live_soperator_release_for_reconcile", lambda **kw: "1.22.3")
    monkeypatch.setattr(
        cli, "_paths_for_target_flux_dir", lambda *a: SimpleNamespace(flux_dir=tmp_path)
    )
    monkeypatch.setattr(cli, "_rendered_soperator_upstream_values", lambda *a: {"desired": True})
    calls = []

    def values(*a, **kw):
        if case == "forbidden":
            raise RuntimeError("Forbidden")
        return None

    def storage(*a, **kw):
        calls.append("storage")
        assert kw["kube_env"] == env
        if case == "missing-storage":
            raise RuntimeError("Protected storage is absent")

    monkeypatch.setattr(source, "observe_upstream_values", values)
    monkeypatch.setattr(source, "verify_reconcile_storage", storage)
    executor = deployment_cli._CliDeploymentExecutor(
        payload, SimpleNamespace(), {}, options=deployment_cli.DeployOptions(), lease=None
    )
    if case == "repair":
        release, absent, identities = executor.observe(None)
        assert (
            release == "1.22.3" and not absent and identities["cluster"]["kubernetes_uid"] == "uid"
        )
        assert executor.observed_reconcile and executor.settings_drift
        assert calls == ["storage"]
        assert not hasattr(executor, "observed_source")
    else:
        with pytest.raises(RuntimeError, match="requires the current|storage is absent|Forbidden"):
            executor.observe(None)
        assert not getattr(executor, "observed_reconcile", False)


@pytest.mark.parametrize(
    "case",
    [
        "equivalent",
        "cluster",
        "selector",
        "partition",
        "added",
        "unavailable",
        "first-install",
        "upgrade",
        "topology",
    ],
)
def test_observe_compares_resolved_settings_without_changing_generation(
    tmp_path, monkeypatch, case
):
    from nebius_cxcli import cli, deployment_cli, deployment_resolution

    payload = config()
    payload["infra"]["components"] = []
    target = {"target_ref": "cluster", "ownership": "onboarded"}
    env = {
        cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "cluster-id",
        cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "ctx",
    }
    desired = {
        "observability": {"clusterId": "cluster-id"},
        "slurmCluster": {
            "overrideValues": {
                "k8sNodeFilters": [
                    {
                        "name": "cpu",
                        "affinity": {
                            "nodeAffinity": {
                                "requiredDuringSchedulingIgnoredDuringExecution": {
                                    "nodeSelectorTerms": [
                                        {
                                            "matchExpressions": [
                                                {
                                                    "key": "group",
                                                    "operator": "In",
                                                    "values": ["system", "accounting"],
                                                }
                                            ]
                                        }
                                    ]
                                }
                            }
                        },
                    }
                ],
                "partitionConfiguration": {
                    "configType": "structured",
                    "partitions": [{"name": "gpu", "config": "Default=YES State=UP"}],
                },
            }
        },
    }
    live = copy.deepcopy(desired)
    override = live["slurmCluster"]["overrideValues"]
    members = override["k8sNodeFilters"][0]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
    members.reverse()
    override["partitionConfiguration"]["partitions"][0]["config"] = (
        "State=UP AllowGroups=ALL Default=YES"
    )
    if case == "cluster":
        live["observability"]["clusterId"] = "foreign-cluster"
    elif case == "selector":
        members.append("foreign-group")
    elif case == "partition":
        override["partitionConfiguration"]["partitions"][0]["config"] = (
            "State=UP AllowGroups=restricted Default=YES"
        )
    elif case == "added":
        live["unexpected"] = True
    raw = copy.deepcopy(desired)
    raw["observability"]["clusterId"] = "unresolved-output"
    assert raw != live

    def generation(values):
        document = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "terraform-fluxcd-values", "namespace": "flux-system"},
            "data": {"values.yaml": yaml.safe_dump(values)},
        }
        return DeploymentGeneration(
            {
                "runtime_config": payload,
                "deploy": {"targets": [{"target_ref": "cluster", "flux_dir": "flux"}]},
            },
            {
                "flux/kustomization.yaml": base64.b64encode(b"resources: [values.yaml]\n").decode(),
                "flux/values.yaml": base64.b64encode(yaml.safe_dump(document).encode()).decode(),
            },
        )

    frozen = generation(raw)
    before = copy.deepcopy(frozen)
    calls = []

    def resolve(actual_cli, original, paths, **kwargs):
        calls.append("resolve")
        assert actual_cli is cli and original is frozen
        assert kwargs == {"initialize_terraform": False, "target_ref": "cluster"}
        if case == "unavailable":
            raise RuntimeError("Required output unavailable")
        return generation(desired)

    monkeypatch.setattr(deployment_resolution, "resolved_application_generation", resolve)
    monkeypatch.setattr(cli, "_resolve_selected_deploy_targets", lambda *a, **kw: [target])
    monkeypatch.setattr(
        cli, "_generated_bundle_mk8s_module_index", lambda _: {"cluster": (None, "cluster")}
    )
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", lambda *a, **kw: env)
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **kw: "uid")
    monkeypatch.setattr(
        cli,
        "_live_soperator_release_for_reconcile",
        lambda **kw: (
            None if case == "first-install" else "1.21.0" if case == "upgrade" else "1.22.3"
        ),
    )
    monkeypatch.setattr(
        cli, "_paths_for_target_flux_dir", lambda *a: SimpleNamespace(flux_dir=tmp_path)
    )
    monkeypatch.setattr(cli, "_rendered_soperator_upstream_values", lambda *a: raw)
    monkeypatch.setattr(source, "observe_upstream_values", lambda *a, **kw: live)
    monkeypatch.setattr(
        source, "verify_reconcile_storage", lambda *a, **kw: calls.append("storage")
    )
    monkeypatch.setattr(
        source, "project_observed_source", lambda *a, **kw: calls.append("project") or payload
    )
    monkeypatch.setattr(source, "observe_adapter", lambda *a, **kw: {})
    monkeypatch.setattr(source, "verify_source_projection", lambda *a, **kw: payload)
    executor = deployment_cli._CliDeploymentExecutor(
        payload, SimpleNamespace(), {}, options=deployment_cli.DeployOptions(), lease=None
    )
    executor.generation = frozen
    if case == "topology":
        executor.observation_plan = {
            "resource_changes": [
                {"address": "module.cluster.node_group", "change": {"actions": ["update"]}}
            ]
        }
    if case == "unavailable":
        with pytest.raises(RuntimeError, match="Required output unavailable"):
            executor.observe(None)
        assert not getattr(executor, "observed_reconcile", False)
    else:
        executor.observe(None)
        if case in {"first-install", "upgrade", "topology"}:
            assert "resolve" not in calls and "storage" not in calls
            assert ("project" in calls) == (case != "first-install")
        else:
            assert executor.observed_reconcile
            assert executor.settings_drift is (case != "equivalent")
            assert calls == ["storage", "resolve"]
    assert frozen == before


@pytest.mark.parametrize("changed_capacity", [False, True])
def test_source_roundtrip_also_requires_independent_protected_resource_match(
    tmp_path, monkeypatch, changed_capacity
):
    from nebius_cxcli.soperator_adapter import compile_upstream_soperator_values
    from test_deployment_campaign import paths

    old = _values()
    upstream, _ = compile_upstream_soperator_values(old, release=_RELEASE)
    adapter_docs, _ = render_soperator_adapter_documents(old, release=_RELEASE)
    adapter = soperator_adapter_state_from_documents(adapter_docs)
    graph = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "nebius-cxcli-soperator-release-graph", "namespace": "flux-system"},
        "data": {"graph.json": json.dumps({"releases": []})},
    }
    outer = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "soperator", "namespace": "flux-system"},
        "spec": {"values": {}},
    }
    inventory = {
        (doc["kind"], doc["metadata"]["name"]): doc for doc in [*adapter_docs, graph, outer]
    }
    source_config = config()
    source_config["apps"]["charts"][0]["values"] = copy.deepcopy(old)
    if changed_capacity:
        source_config["apps"]["charts"][0]["values"]["volume"]["jail"]["size"] = "2Gi"
    rendered = {}
    cleaned = []

    def render(*, source_payload, **kwargs):
        stage = paths(tmp_path / str(len(rendered)))
        stage.flux_dir.mkdir(parents=True)
        stage.reports_dir.mkdir()
        values = source_payload["apps"]["charts"][0]["values"]
        docs, _ = render_soperator_adapter_documents(values, release=_RELEASE)
        compiled, _ = compile_upstream_soperator_values(values, release=_RELEASE)
        (stage.flux_dir / "kustomization.yaml").write_text("resources: [adapter.yaml]\n")
        (stage.flux_dir / "adapter.yaml").write_text(yaml.safe_dump_all([*docs, graph, outer]))
        manifest = {
            "runtime_config": copy.deepcopy(source_payload),
            "deploy": {"targets": [{"target_ref": "cluster", "flux_dir": str(stage.flux_dir)}]},
        }
        rendered[stage.generated_dir] = (
            stage,
            manifest,
            compiled,
            soperator_adapter_state_from_documents(docs),
        )
        return SimpleNamespace(staged_paths=stage, cleanup=lambda: cleaned.append(stage))

    def read(namespace, args, **kwargs):
        doc = copy.deepcopy(inventory[(args[1].split(".")[0], args[2])])
        doc["metadata"]["uid"] = "uid-" + args[2]
        if doc["kind"] == "HelmRelease":
            doc["metadata"]["generation"] = 1
            doc["status"] = {
                "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 1}]
            }
        return SimpleNamespace(stdout=json.dumps(doc))

    cli = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _render_soperator_upgrade_admission=render,
        load_generated_manifest=lambda directory: rendered[directory][1],
        _resolve_selected_deploy_targets=lambda manifest, **kw: manifest["deploy"]["targets"],
        _paths_for_target_flux_dir=lambda stage, target: stage,
        _rendered_soperator_upstream_values=lambda directory: rendered[directory.parent][2],
        _rendered_soperator_adapter_state=lambda directory: rendered[directory.parent][3],
        _run_soperator_upgrade_kubectl=read,
    )
    monkeypatch.setattr(
        "nebius_cxcli.soperator_release.load_soperator_release_snapshot", lambda _: _RELEASE
    )
    monkeypatch.setattr(
        "nebius_cxcli.flux_ops.stable_soperator_documents", lambda docs, releases: docs
    )
    physical = []
    monkeypatch.setattr(
        "nebius_cxcli.deployment_jail_state.observe_jail_storage",
        lambda *a, **kw: physical.append(True),
    )
    kwargs = dict(
        paths=paths(tmp_path / "project"),
        target_ref="cluster",
        upstream=upstream,
        adapter=adapter,
        kube_env={"context": "ctx"},
    )
    if changed_capacity:
        with pytest.raises(RuntimeError, match="settings have not converged"):
            source.verify_source_projection(cli, source_config, **kwargs)
        assert not physical
    else:
        source.verify_source_projection(cli, source_config, **kwargs)
        assert physical == [True]
    assert len(cleaned) == 2
