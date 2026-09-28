from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli, deployment_cli, mk8s_upgrade
from nebius_cxcli.deployment_observation import (
    observe_release_settings,
    rendered_release_identities,
)
from nebius_cxcli.deployment_state import digest
from test_deployment_plan import config


def test_settings_observation_keeps_complete_digest_and_rejects_unknown_reads():
    payload = {
        "metadata": {"name": "release", "namespace": "flux-system", "uid": "u"},
        "spec": {"values": {"arbitrary": {"setting": 1}}},
    }
    fake = SimpleNamespace(GRAFANA_TARGET_KUBE_CONTEXT_ENV="context")
    calls = []

    def read(namespace, args, **kwargs):
        calls.append((namespace, args, kwargs))
        return SimpleNamespace(stdout=json.dumps(payload))

    fake._run_soperator_upgrade_kubectl = read
    kwargs = {"kube_env": {"context": "ctx"}, "identities": [("flux-system", "release")]}
    first = observe_release_settings(fake, **kwargs)
    assert first == {"flux-system/release": {"uid": "u", "specSha256": digest(payload["spec"])}}
    payload["spec"]["values"]["arbitrary"]["setting"] = 2
    assert observe_release_settings(fake, **kwargs) != first
    assert calls[0][2]["check"] is True
    assert "--ignore-not-found=true" in calls[0][1]
    fake._run_soperator_upgrade_kubectl = lambda *a, **kw: SimpleNamespace(stdout="")
    assert observe_release_settings(fake, **kwargs) == {"flux-system/release": {"missing": True}}

    def unavailable(*args, **kwargs):
        raise RuntimeError("unavailable")

    fake._run_soperator_upgrade_kubectl = unavailable
    with pytest.raises(RuntimeError, match="unavailable"):
        observe_release_settings(fake, **kwargs)


def test_graph_uses_flux_object_namespace_and_includes_outer_release(tmp_path):
    (tmp_path / "outer.yaml").write_text(
        "kind: HelmRelease\nmetadata:\n  name: outer\n  namespace: flux-system\n"
    )
    graph = {"releases": [{"namespace": "slurm", "releaseName": "child"}]}
    assert rendered_release_identities(tmp_path, graph) == (
        ("flux-system", "child"),
        ("flux-system", "outer"),
    )


@pytest.mark.parametrize("ordinary_first", [False, True])
@pytest.mark.parametrize("conflict", [None, "labels", "annotations", "spec"])
def test_target_inventory_preserves_shared_namespace_owner(ordinary_first, conflict):
    import base64

    import yaml

    from nebius_cxcli.deployment_observation import target_documents

    protected = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": "soperator",
            "labels": {"soperator.nebius.ai/lifecycle": "shared-adopted"},
        },
    }
    ordinary = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": "soperator", "annotations": {"cxcli.nebius.com/app-owner": "project"}},
    }
    if conflict == "spec":
        ordinary["spec"] = {"finalizers": ["custom"]}
    elif conflict:
        ordinary["metadata"].setdefault(conflict, {})["custom"] = "value"
    resources = ["ordinary", "namespace.yaml"] if ordinary_first else ["namespace.yaml", "ordinary"]
    files = {
        "flux/kustomization.yaml": {"resources": resources},
        "flux/namespace.yaml": protected,
        "flux/ordinary/kustomization.yaml": {"resources": ["namespace.yaml"]},
        "flux/ordinary/namespace.yaml": ordinary,
    }
    generation = SimpleNamespace(
        manifest={"deploy": {"targets": [{"target_ref": "cluster", "flux_dir": "flux"}]}},
        files={
            name: base64.b64encode(yaml.safe_dump(value).encode()).decode()
            for name, value in files.items()
        },
    )
    if conflict:
        with pytest.raises(RuntimeError, match="identity is ambiguous"):
            target_documents(generation, "cluster")
    else:
        assert target_documents(generation, "cluster") == {"/Namespace//soperator": protected}


@pytest.mark.parametrize("outcome", ["absent", "exists", "unavailable"])
def test_empty_terraform_state_requires_independent_provider_absence(monkeypatch, outcome):
    desired = config()
    runtime = SimpleNamespace(
        client_info=SimpleNamespace(nebius=SimpleNamespace(project_id="project"))
    )
    monkeypatch.setattr(deployment_cli, "to_plain_data", lambda _: desired)
    monkeypatch.setattr(cli, "_config_has_enabled_infra_components", lambda _: True)
    monkeypatch.setattr(cli, "terraform_show_json", lambda *a, **kw: {})
    monkeypatch.setattr(
        deployment_cli._CliDeploymentExecutor,
        "_terraform_plan",
        lambda *a, **kw: (None, {"resource_changes": []}),
    )
    monkeypatch.setattr(cli, "_terraform_state_resources", lambda _: [])
    monkeypatch.setattr(
        cli,
        "_resolve_selected_deploy_targets",
        lambda *a, **kw: [{"target_ref": "cluster", "ownership": "managed"}],
    )
    closed = []
    monkeypatch.setattr(
        cli, "init_nebius_sdk", lambda **kw: SimpleNamespace(sync_close=lambda: closed.append(True))
    )
    calls = []

    class Provider:
        def __init__(self, sdk):
            pass

        def get_cluster_by_name(self, **kwargs):
            calls.append(kwargs)
            if outcome == "absent":
                raise RuntimeError("NOT_FOUND")
            if outcome == "unavailable":
                raise RuntimeError("permission denied")
            return object()

    monkeypatch.setattr(mk8s_upgrade, "Mk8sKubernetesVersionExecutor", Provider)
    executor = deployment_cli._CliDeploymentExecutor(
        runtime,
        SimpleNamespace(infra_dir="infra"),
        {},
        options=deployment_cli.DeployOptions(),
        lease=None,
    )
    if outcome == "absent":
        assert executor.observe(None) == (None, True, {})
    else:
        with pytest.raises(RuntimeError, match="no authoritative Terraform ownership|Cannot prove"):
            executor.observe(None)
    assert closed == [True]
    assert calls == [{"project_id": "project", "name": "cluster"}]


def test_external_chart_transition_freezes_current_version_and_reuses_it_on_recovery(monkeypatch):
    desired = config()
    desired["infra"]["components"] = []
    desired["deploy"] = {
        "targets": [{"instance_id": "cluster", "kind": "external-mk8s", "cluster_id": "mk8s"}]
    }
    runtime = SimpleNamespace(
        client_info=SimpleNamespace(nebius=SimpleNamespace(project_id="project"))
    )
    executor = deployment_cli._CliDeploymentExecutor(
        runtime,
        SimpleNamespace(config_path="config"),
        {},
        options=deployment_cli.DeployOptions(),
        lease=None,
    )
    executor.generation = SimpleNamespace(manifest={"runtime_config": desired})
    executor.plan = SimpleNamespace(target_ref="cluster", target_release="1.23.0")
    closed = []
    monkeypatch.setattr(
        cli, "init_nebius_sdk", lambda **kw: SimpleNamespace(sync_close=lambda: closed.append(True))
    )
    cluster = SimpleNamespace(
        metadata=SimpleNamespace(id="mk8s", parent_id="project"),
        spec=SimpleNamespace(control_plane=SimpleNamespace(version="1.34")),
    )
    monkeypatch.setattr(
        cli,
        "Mk8sKubernetesVersionExecutor",
        lambda sdk: SimpleNamespace(get_cluster=lambda id: copy.deepcopy(cluster)),
    )
    assert executor._campaign_kwargs()["to_k8s_version"] == "1.34"
    assert closed == [True]
    executor.campaign_intent = SimpleNamespace(requested_kubernetes_selector="1.34")
    monkeypatch.setattr(
        cli, "init_nebius_sdk", lambda **kw: pytest.fail("recovery must use frozen version")
    )
    assert executor._campaign_kwargs()["to_k8s_version"] == "1.34"


@pytest.mark.parametrize(
    ("strategy", "surge", "error"),
    [
        ("zero-surge", 0, None),
        ("force-delete", 0, None),
        ("safe-surge", 2, None),
        ("zero-surge", False, "must be an integer"),
        ("zero-surge", True, "must be an integer"),
        ("zero-surge", "0", "must be an integer"),
        ("zero-surge", 0.0, "must be an integer"),
        ("zero-surge", 1, "only with --strategy safe-surge"),
        ("force-delete", -1, "only with --strategy safe-surge"),
        ("safe-surge", 0, "greater than 0"),
    ],
)
def test_saved_rollout_round_trips_through_campaign_admission(strategy, surge, error):
    import yaml

    desired = config()
    desired["deploy"] = {
        "targets": [
            {
                "instance_id": "cluster",
                "soperator_rollout": {"strategy": strategy, "max_surge_count": surge},
            }
        ]
    }
    executor = deployment_cli._CliDeploymentExecutor(
        SimpleNamespace(),
        SimpleNamespace(config_path="config"),
        {},
        options=deployment_cli.DeployOptions(),
        lease=None,
    )
    executor.generation = SimpleNamespace(
        manifest={"runtime_config": yaml.safe_load(yaml.safe_dump(desired))}
    )
    executor.plan = SimpleNamespace(target_ref="cluster", target_release="4.1.8", stages=())
    executor.hooks = SimpleNamespace(source_groups=("worker-a", "worker-b"))
    kwargs = executor._campaign_kwargs()
    if error:
        with pytest.raises(ValueError, match=error):
            mk8s_upgrade.resolve_strategy_max_surge_count(
                kwargs["node_group_strategy"], kwargs["strategy_max_surge_count"]
            )
    else:
        assert (
            mk8s_upgrade.resolve_strategy_max_surge_count(
                kwargs["node_group_strategy"], kwargs["strategy_max_surge_count"]
            )
            == surge
        )
    assert executor.generation.manifest["runtime_config"] == desired


def test_desired_proof_rejects_retained_removed_values_and_stale_readiness():
    import base64

    import yaml

    from nebius_cxcli.deployment_observation import verify_desired_target
    from nebius_cxcli.deployment_state import DeploymentGeneration

    desired = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "release", "namespace": "flux-system"},
        "spec": {"values": {"kept": 2}},
    }
    generation = DeploymentGeneration(
        {"deploy": {"targets": [{"target_ref": "target", "flux_dir": "flux/target"}]}},
        {
            "flux/target/kustomization.yaml": base64.b64encode(
                b"resources: [release.yaml]"
            ).decode(),
            "flux/target/release.yaml": base64.b64encode(yaml.safe_dump(desired).encode()).decode(),
        },
    )
    live = copy.deepcopy(desired)
    live["metadata"].update(uid="u", generation=2)
    live["status"] = {"observedGeneration": 2, "conditions": [{"type": "Ready", "status": "True"}]}
    fake = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _run_soperator_upgrade_kubectl=lambda *a, **kw: SimpleNamespace(stdout=json.dumps(live)),
    )
    kwargs = {"generation": generation, "target_ref": "target", "kube_env": {}}
    assert verify_desired_target(fake, **kwargs)["ready"] is True
    live["spec"]["values"]["removed"] = 1
    with pytest.raises(RuntimeError, match="not converged"):
        verify_desired_target(fake, **kwargs)
    del live["spec"]["values"]["removed"]
    live["status"]["observedGeneration"] = 1
    with pytest.raises(RuntimeError, match="stale"):
        verify_desired_target(fake, **kwargs)


@pytest.mark.parametrize(
    ("live_values", "matches"),
    [
        ({"observability": {"enabled": True}}, True),
        ({"observability": {"enabled": True, "overrideValues": None}}, True),
        ({"observability": {"enabled": True, "overrideValues": {"stale": True}}}, False),
        ({"observability": {"enabled": True, "unexpected": None}}, False),
        ({"observability": {}}, False),
        ({}, False),
    ],
)
def test_desired_proof_accepts_apply_null_deletion_only(live_values, matches):
    import base64

    import yaml

    from nebius_cxcli.deployment_observation import verify_desired_target
    from nebius_cxcli.deployment_state import DeploymentGeneration

    desired = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "release", "namespace": "flux-system"},
        "spec": {"values": {"observability": {"enabled": True, "overrideValues": None}}},
    }
    generation = DeploymentGeneration(
        {"deploy": {"targets": [{"target_ref": "target", "flux_dir": "flux/target"}]}},
        {
            "flux/target/kustomization.yaml": base64.b64encode(
                b"resources: [release.yaml]"
            ).decode(),
            "flux/target/release.yaml": base64.b64encode(yaml.safe_dump(desired).encode()).decode(),
        },
    )
    live = copy.deepcopy(desired)
    live["metadata"].update(uid="u", generation=2)
    live["status"] = {"observedGeneration": 2, "conditions": [{"type": "Ready", "status": "True"}]}
    live["spec"]["values"] = live_values
    fake = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _run_soperator_upgrade_kubectl=lambda *a, **kw: SimpleNamespace(stdout=json.dumps(live)),
    )
    if matches:
        assert verify_desired_target(fake, generation=generation, target_ref="target", kube_env={})[
            "ready"
        ]
    else:
        with pytest.raises(RuntimeError, match="not converged"):
            verify_desired_target(fake, generation=generation, target_ref="target", kube_env={})


def test_apply_null_semantics_preserve_atomic_lists_and_embedded_yaml():
    from nebius_cxcli.deployment_observation import _owned_matches

    assert not _owned_matches(
        {"values": {"rows": [{"nullable": None}]}}, {"values": {"rows": [{}]}}
    )
    assert not _owned_matches(
        {"data": {"values.yaml": "nullable: null\n"}}, {"data": {"values.yaml": "{}\n"}}
    )


def test_oci_repository_without_status_and_removed_resource_proof():
    import base64

    import yaml

    from nebius_cxcli.deployment_observation import verify_desired_target
    from nebius_cxcli.deployment_state import DeploymentGeneration
    from nebius_cxcli.flux_render import _helm_repository_doc

    document = _helm_repository_doc(
        "charts", "oci://registry.example.invalid/charts", repo_type="oci"
    )
    generation = DeploymentGeneration(
        {"deploy": {"targets": [{"target_ref": "target", "flux_dir": "flux/target"}]}},
        {
            "flux/target/kustomization.yaml": base64.b64encode(b"resources: [repo.yaml]").decode(),
            "flux/target/repo.yaml": base64.b64encode(yaml.safe_dump(document).encode()).decode(),
        },
    )
    live = copy.deepcopy(document)
    live["metadata"].update(uid="u", generation=1)
    fake = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _run_soperator_upgrade_kubectl=lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps(live) if live else ""
        ),
    )
    proof = verify_desired_target(fake, generation=generation, target_ref="target", kube_env={})
    assert len(proof["resources"]) == 1
    removed = DeploymentGeneration(
        generation.manifest,
        {"flux/target/kustomization.yaml": base64.b64encode(b"resources: []").decode()},
    )
    with pytest.raises(RuntimeError, match="Removed owned resource remains"):
        verify_desired_target(
            fake, generation=removed, previous=generation, target_ref="target", kube_env={}
        )
    live.clear()
    assert verify_desired_target(
        fake, generation=removed, previous=generation, target_ref="target", kube_env={}
    )["ready"]
    with pytest.raises(RuntimeError, match="kustomization is missing"):
        verify_desired_target(
            fake,
            generation=DeploymentGeneration(generation.manifest, {}),
            target_ref="target",
            kube_env={},
        )


def test_desired_soperator_documents_share_stable_executor_projection():
    import base64

    import yaml

    from nebius_cxcli import flux_ops
    from nebius_cxcli.deployment_observation import target_documents, verify_desired_target
    from nebius_cxcli.deployment_state import DeploymentGeneration
    from test_soperator_flux_sources import _outer_bundle, _staged_contract

    outer = yaml.safe_load(_outer_bundle())
    graph = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "nebius-cxcli-soperator-release-graph", "namespace": "flux-system"},
        "data": {"graph.json": json.dumps(_staged_contract())},
    }
    documents = [outer, graph]
    frozen = DeploymentGeneration(
        {"deploy": {"targets": [{"target_ref": "cluster", "flux_dir": "flux/cluster"}]}},
        {
            "flux/cluster/kustomization.yaml": base64.b64encode(
                b"resources: [bundle.yaml]"
            ).decode(),
            "flux/cluster/bundle.yaml": base64.b64encode(
                yaml.safe_dump_all(documents).encode()
            ).decode(),
        },
    )
    stable = flux_ops.stable_soperator_documents(documents, _staged_contract()["releases"])
    expected = target_documents(frozen, "cluster")
    assert list(expected.values()) == stable and stable != documents
    live = {doc["metadata"]["name"]: copy.deepcopy(doc) for doc in stable}
    for doc in live.values():
        doc["metadata"].update(uid="u", generation=2)
        doc["status"] = {
            "observedGeneration": 2,
            "conditions": [{"type": "Ready", "status": "True"}],
        }
    fake = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _run_soperator_upgrade_kubectl=lambda namespace, argv, **kw: SimpleNamespace(
            stdout=json.dumps(live[argv[2]])
        ),
    )
    assert verify_desired_target(fake, generation=frozen, target_ref="cluster", kube_env={})[
        "ready"
    ]
    live[outer["metadata"]["name"]]["spec"]["postRenderers"] = outer["spec"]["postRenderers"]
    with pytest.raises(RuntimeError, match="settings have not converged"):
        verify_desired_target(fake, generation=frozen, target_ref="cluster", kube_env={})


@pytest.mark.parametrize("phase", ["schedules", "ready"])
def test_desired_proof_uses_real_checks_phase_projection(phase):
    import base64

    import yaml

    from nebius_cxcli import flux_ops
    from nebius_cxcli.deployment_observation import verify_desired_target
    from nebius_cxcli.deployment_state import DeploymentGeneration
    from nebius_cxcli.soperator_checks_campaign import SoperatorCampaignChecks
    from nebius_cxcli.soperator_checks_phase import ChecksPhase, ChecksPhaseContext
    from nebius_cxcli.soperator_checks_policy import SoperatorChecksPolicy, checks_digest
    from test_soperator_flux_sources import _outer_bundle, _staged_contract

    partitions = {
        "configType": "structured",
        "partitions": [
            {"name": "gpu", "config": "Default=YES State=UP"},
            {"name": "hidden", "config": "Hidden=YES State=UP"},
        ],
    }
    values = {"slurmCluster": {"overrideValues": {"partitionConfiguration": partitions}}}
    policy = SoperatorChecksPolicy("source", checks_digest(values), (), {}, partitions=partitions)
    context = ChecksPhaseContext(
        ChecksPhase(phase),
        "reserve",
        partition_restoration={"gpu": {"State": "INACTIVE"}} if phase == "ready" else None,
    )
    owner = object.__new__(SoperatorCampaignChecks)
    owner.load_target = lambda: (policy, (), ())
    owner._execution = lambda *a: SimpleNamespace(
        require_lifecycle=lambda: SimpleNamespace(context=lambda: context)
    )
    projection = owner.documents_projection()
    outer = yaml.safe_load(_outer_bundle())
    outer["spec"]["values"] = copy.deepcopy(values)
    graph = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "nebius-cxcli-soperator-release-graph", "namespace": "flux-system"},
        "data": {"graph.json": json.dumps(_staged_contract())},
    }
    value_map = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "terraform-fluxcd-values", "namespace": "flux-system"},
        "data": {"values.yaml": yaml.safe_dump(values)},
    }
    documents = [outer, graph, value_map]
    frozen = DeploymentGeneration(
        {"deploy": {"targets": [{"target_ref": "cluster", "flux_dir": "flux/cluster"}]}},
        {
            "flux/cluster/kustomization.yaml": base64.b64encode(
                b"resources: [bundle.yaml]"
            ).decode(),
            "flux/cluster/bundle.yaml": base64.b64encode(
                yaml.safe_dump_all(documents).encode()
            ).decode(),
        },
    )
    observed = projection(
        flux_ops.stable_soperator_documents(documents, _staged_contract()["releases"]),
        _staged_contract()["releases"],
    )
    live = {doc["metadata"]["name"]: doc for doc in observed}
    for doc in live.values():
        doc["metadata"].update(uid="u", generation=2)
        doc["status"] = {
            "observedGeneration": 2,
            "conditions": [{"type": "Ready", "status": "True"}],
        }
    fake = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _run_soperator_upgrade_kubectl=lambda ns, argv, **kw: SimpleNamespace(
            stdout=json.dumps(live[argv[2]])
        ),
    )
    with pytest.raises(RuntimeError, match="settings have not converged"):
        verify_desired_target(fake, generation=frozen, target_ref="cluster", kube_env={})
    assert verify_desired_target(
        fake, generation=frozen, target_ref="cluster", kube_env={}, documents_projection=projection
    )["ready"]
    live[outer["metadata"]["name"]]["spec"]["values"]["unadmitted"] = True
    with pytest.raises(RuntimeError, match="settings have not converged"):
        verify_desired_target(
            fake,
            generation=frozen,
            target_ref="cluster",
            kube_env={},
            documents_projection=projection,
        )


@pytest.mark.parametrize("field", ["labels", "annotations", "ownerReferences"])
def test_protected_observation_rejects_ownership_metadata_drift(monkeypatch, field):
    from nebius_cxcli import deployment_observation as observation

    desired = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "owned",
            "namespace": "soperator",
            "labels": {"owner": "cxcli"},
            "annotations": {"release": "accepted"},
        },
        "data": {"key": "value"},
    }
    live = copy.deepcopy(desired)
    live["metadata"]["uid"] = "uid"
    live["metadata"][field] = (
        [{"uid": "foreign", "controller": True}]
        if field == "ownerReferences"
        else {"owner": "foreign"}
    )
    monkeypatch.setattr(observation, "target_documents", lambda *a, **kw: {"owned": desired})
    fake = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _run_soperator_upgrade_kubectl=lambda *a, **kw: SimpleNamespace(stdout=json.dumps(live)),
    )
    with pytest.raises(RuntimeError, match="ownership changed"):
        observation.verify_desired_target(
            fake,
            generation=object(),
            target_ref="cluster",
            kube_env={"context": "ctx"},
            protected_only=True,
        )


@pytest.mark.parametrize("rendered", ["cluster", "mk8s-id", "foreign"])
def test_observation_resolves_only_renderer_owned_cluster_identity(monkeypatch, rendered):
    import yaml

    from nebius_cxcli.deployment_observation import bind_soperator_observation_identity

    values = {
        "observability": {
            "clusterId": rendered,
            "other": "preserved",
            "vmStack": {
                "values": {"vmagent": {"spec": {"externalLabels": {"mk8s_cluster_id": rendered}}}}
            },
        }
    }
    document = {
        "kind": "ConfigMap",
        "metadata": {"name": "terraform-fluxcd-values"},
        "data": {"values.yaml": yaml.safe_dump(values)},
    }
    monkeypatch.setattr("nebius_cxcli.flux_ops._staged_soperator_outer_release", lambda *a: None)
    if rendered == "foreign":
        with pytest.raises(RuntimeError, match="identity conflicts"):
            bind_soperator_observation_identity(
                [document], [], target_ref="cluster", cluster_id="mk8s-id"
            )
    else:
        result = bind_soperator_observation_identity(
            [document], [], target_ref="cluster", cluster_id="mk8s-id"
        )
        bound = yaml.safe_load(result[0]["data"]["values.yaml"])
        assert bound["observability"]["clusterId"] == "mk8s-id"
        assert bound["observability"]["other"] == "preserved"
        assert yaml.safe_load(document["data"]["values.yaml"]) == values
