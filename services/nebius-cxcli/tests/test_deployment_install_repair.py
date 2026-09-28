import base64
import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import deployment_install_repair as repair
from nebius_cxcli.deployment_applications import ApplicationJournal, target_bundle_digest
from nebius_cxcli.deployment_state import DeploymentGeneration, digest
from nebius_cxcli.soperator_install_docker_storage_repair import docker_storage_candidate
from test_soperator_install_docker_storage_repair import compiled
from test_soperator_upstream_adapter import _values, render_soperator_adapter_documents


def bundles():
    files, _ = compiled()
    for index, (name, raw) in enumerate(files.items()):
        doc = yaml.safe_load(raw)
        doc.update(apiVersion="v1", kind="ConfigMap", metadata={"name": f"object-{index}"})
        files[name] = yaml.safe_dump(doc, sort_keys=False).encode()
    files["kustomization.yaml"] = yaml.safe_dump({"resources": list(files)}).encode()

    def generation(content):
        return DeploymentGeneration(
            {"deploy": {"targets": [{"target_ref": "cluster", "flux_dir": "flux/cluster"}]}},
            {"flux/cluster/" + k: base64.b64encode(v).decode() for k, v in content.items()},
        )

    old = generation(files)
    new = generation(docker_storage_candidate(files))
    return old, new, {"status": "executing", "desiredBundle": target_bundle_digest(old, "cluster")}


@pytest.mark.parametrize("committed_files", [False, True])
def test_bundle_transition_keeps_authenticated_predecessor_across_crash(committed_files):
    old, new, entry = bundles()
    result = repair.storage_input_transition(
        current=new if committed_files else old, desired=new, target_ref="cluster", entry=entry
    )
    assert result.previous_bundle == entry["desiredBundle"]
    assert result.desired_bundle == target_bundle_digest(new, "cluster")
    assert set(result.previous_files) == set(result.replacement_files)


def test_unchanged_install_keeps_bundle_identity_without_retained_rootfs_generations():
    from nebius_cxcli.flux_render import _multi_doc_yaml

    # This checkpoint already has private Docker storage. Rendering an unused
    # rootfs-retention feature must not turn its resume into another repair.
    _, current, entry = bundles()
    documents, _ = render_soperator_adapter_documents(_values())
    key = "flux/cluster/soperator-nebius-adapter.yaml"
    desired = replace(
        current,
        files={
            **current.files,
            key: base64.b64encode(_multi_doc_yaml(documents).encode()).decode(),
        },
    )
    recorded = copy.deepcopy(documents)
    state_doc = next(doc for doc in recorded if "state.json" in doc.get("data", {}))
    state = json.loads(state_doc["data"]["state.json"])
    # The recorded contract has no retained-generation field when none exist.
    state.pop("retainedGenerations", None)
    state_doc["data"]["state.json"] = json.dumps(state, indent=2, sort_keys=True)
    state_doc["metadata"]["annotations"]["soperator.nebius.ai/state-sha256"] = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    current = replace(
        current,
        files={
            **current.files,
            key: base64.b64encode(_multi_doc_yaml(recorded).encode()).decode(),
        },
    )
    entry["desiredBundle"] = target_bundle_digest(current, "cluster")

    assert (
        repair.storage_input_transition(
            current=current, desired=desired, target_ref="cluster", entry=entry
        )
        is None
    )


@pytest.mark.parametrize(
    "mutation", ["prior", "desired", "complete", "unrelated", "another_repair"]
)
def test_bundle_transition_rejects_drift(mutation):
    old, new, entry = bundles()
    if mutation == "prior":
        entry["desiredBundle"] = digest("wrong")
    if mutation in {"desired", "unrelated"}:
        files = dict(new.files)
        key = "flux/cluster/soperator-nebius-adapter.yaml"
        doc = yaml.safe_load(base64.b64decode(files[key]))
        doc["data"] = {"unrelated": "change"}
        files[key] = base64.b64encode(yaml.safe_dump(doc).encode()).decode()
        new = replace(new, files=files)
    if mutation == "complete":
        entry["status"] = "complete"
    if mutation == "another_repair":
        entry["inputRepair"] = {"already": True}
    with pytest.raises(RuntimeError):
        repair.storage_input_transition(current=old, desired=new, target_ref="cluster", entry=entry)


def test_application_journal_requires_explicit_repair_and_retains_prior_bundle(tmp_path):
    journal = ApplicationJournal(
        tmp_path / "journal.json", generation=digest("generation"), selected=["cluster"]
    )
    identity = {"cluster_id": "cluster-id", "kubernetes_uid": "uid"}
    old, new = digest("old"), digest("new")
    journal.bind("cluster", identity, old)
    with pytest.raises(RuntimeError, match="checkpointed"):
        journal.bind("cluster", identity, new)
    before = copy.deepcopy(journal.payload)
    with pytest.raises(RuntimeError, match="predecessor"):
        journal.repair_inputs(
            "cluster",
            identity={**identity, "kubernetes_uid": "other"},
            previous=old,
            desired=new,
            admission_sha256=digest("seal"),
        )
    assert journal.payload == before
    journal.repair_inputs(
        "cluster", identity=identity, previous=old, desired=new, admission_sha256=digest("seal")
    )
    journal.bind("cluster", identity, new)
    restored = ApplicationJournal(
        journal.path, generation=digest("generation"), selected=["cluster"]
    )
    assert restored.entry("cluster")["inputRepair"] == {
        "reason": "install-worker-docker-storage-v1",
        "previousBundle": old,
        "admissionSha256": digest("seal"),
    }


def test_rejected_cluster_admission_does_not_rebind_application(tmp_path, monkeypatch):
    old, new, entry = bundles()
    transition = repair.storage_input_transition(
        current=old, desired=new, target_ref="cluster", entry=entry
    )
    (tmp_path / "soperator-slurm-actions-cluster.json").write_text("{}")
    monkeypatch.setattr(repair, "read_owner_only_json", lambda *a, **kw: {})
    monkeypatch.setattr(repair, "_files", lambda _: {})
    monkeypatch.setattr(repair, "_file_hashes", lambda _: transition.previous_files)
    monkeypatch.setattr(repair, "prepare_install_docker_storage_repair", lambda **kw: None)
    cli = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _paths_for_target_flux_dir=lambda *a: SimpleNamespace(
            reports_dir=tmp_path, flux_dir=tmp_path
        ),
        _read_soperator_slurm_cluster_journal=lambda **kw: ({}, "version"),
    )
    executor = SimpleNamespace(
        cli=cli,
        paths=None,
        _selected_target=lambda _: {},
        _install_input_transitions={"cluster": transition},
        _application_journal=lambda: pytest.fail("must not publish unsealed bundle"),
    )
    with pytest.raises(RuntimeError, match="cluster-sealed"):
        repair.admit_storage_input_transition(
            executor,
            "cluster",
            {"cluster_id": "id", "kubernetes_uid": "uid"},
            {"context": "ctx"},
            lambda: None,
        )


def test_executor_preserves_recovered_bytes_until_native_admission(monkeypatch):
    from nebius_cxcli.deployment_cli import _CliDeploymentExecutor

    old, new, entry = bundles()
    monkeypatch.setattr(DeploymentGeneration, "capture", lambda *args: old)
    journal = SimpleNamespace(payload={"selected": ["cluster"]}, entry=lambda ref: entry)
    executor = SimpleNamespace(
        lease=SimpleNamespace(assert_held=lambda: None),
        paths=None,
        manifest=old.manifest,
        config={},
        _desired_application_generation=lambda **kw: new,
        _application_journal=lambda: journal,
        cli=SimpleNamespace(
            _refresh_flux_after_terraform_outputs=lambda *args: pytest.fail(
                "must not overwrite the authenticated predecessor"
            )
        ),
    )
    _CliDeploymentExecutor._prepare_install_application_inputs(executor)
    assert executor._install_input_transitions["cluster"].previous_bundle == entry["desiredBundle"]


@pytest.mark.parametrize("change", ["graph", "grafana", "same", "storage_repair"])
def test_install_replay_preflight_rejects_unfrozen_telemetry_without_writes(monkeypatch, change):
    from nebius_cxcli.deployment_cli import _CliDeploymentExecutor

    old, repaired, entry = bundles()
    desired = repaired if change == "storage_repair" else old
    if change in {"graph", "grafana"}:
        # Use declared Helm resources so this tests the target's actual bundle identity.
        root = "flux/cluster/"

        def telemetry_generation(generation, enabled):
            files = dict(generation.files)
            kustomization = yaml.safe_load(base64.b64decode(files[root + "kustomization.yaml"]))
            if "telemetry.yaml" not in kustomization["resources"]:
                kustomization["resources"].append("telemetry.yaml")
            files[root + "kustomization.yaml"] = base64.b64encode(
                yaml.safe_dump(kustomization).encode()
            ).decode()
            documents = [
                {
                    "apiVersion": "helm.toolkit.fluxcd.io/v2",
                    "kind": "HelmRelease",
                    "metadata": {"name": "soperator-fluxcd-vm-stack", "namespace": "flux-system"},
                    "spec": {
                        "values": {
                            "grafana": {"enabled": enabled if change == "grafana" else False}
                        }
                    },
                }
            ]
            if change == "graph" and enabled:
                documents.append(
                    {
                        "apiVersion": "helm.toolkit.fluxcd.io/v2",
                        "kind": "HelmRelease",
                        "metadata": {
                            "name": "soperator-fluxcd-dcgm-exporter",
                            "namespace": "flux-system",
                        },
                        "spec": {"values": {}},
                    }
                )
            files[root + "telemetry.yaml"] = base64.b64encode(
                yaml.safe_dump_all(documents).encode()
            ).decode()
            return replace(generation, files=files)

        old = telemetry_generation(old, False)
        desired = telemetry_generation(old, True)
        entry["desiredBundle"] = target_bundle_digest(old, "cluster")
    before = copy.deepcopy(entry)
    journal = SimpleNamespace(payload={"selected": ["cluster"]}, entry=lambda ref: entry)
    monkeypatch.setattr(DeploymentGeneration, "capture", lambda *args: old)
    executor = SimpleNamespace(
        paths=object(),
        manifest=old.manifest,
        _application_journal=lambda: journal,
        _desired_application_generation=lambda **kwargs: desired,
    )
    if change in {"same", "storage_repair"}:
        _CliDeploymentExecutor._preflight_install_application_inputs(executor)
    else:
        with pytest.raises(RuntimeError, match="drift"):
            _CliDeploymentExecutor._preflight_install_application_inputs(executor)
    assert entry == before


@pytest.mark.parametrize(
    "change",
    [
        "dcgm",
        "grafana",
        "same",
        "key_order",
        "type",
        "list_order",
        "extra_data",
        "duplicate",
        "invalid",
    ],
)
def test_empty_application_journal_does_not_adopt_a_changed_frozen_bundle(monkeypatch, change):
    from io import StringIO

    from rich.console import Console

    from nebius_cxcli import deployment_resolution
    from nebius_cxcli.deployment_cli import _CliDeploymentExecutor

    policy = {
        "observability": {"enabled": True, "dcgmExporter": {"enabled": True}},
        "slurmCluster": {"enabled": True},
    }
    document = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "terraform-fluxcd-values", "namespace": "flux-system"},
        "data": {"values.yaml": yaml.safe_dump(policy)},
    }

    def generation(doc):
        return DeploymentGeneration(
            {
                "runtime_config": {
                    "apps": {
                        "charts": [{"id": "soperator", "target_ref": "cluster", "enabled": True}]
                    }
                },
                "render": {"application_inputs": {"mk8s.cluster_id": "frozen-id"}},
                "deploy": {"targets": [{"target_ref": "cluster", "flux_dir": "flux/cluster"}]},
            },
            {
                "flux/cluster/kustomization.yaml": base64.b64encode(
                    b"resources: [values.yaml]"
                ).decode(),
                "flux/cluster/values.yaml": base64.b64encode(yaml.safe_dump(doc).encode()).decode(),
            },
        )

    frozen = generation(document)
    desired_doc = copy.deepcopy(document)
    if change == "dcgm":
        policy["observability"]["dcgmExporter"]["enabled"] = False
    elif change == "grafana":
        policy["observability"]["vmStack"] = {"values": {"grafana": {"enabled": True}}}
    desired_doc["data"]["values.yaml"] = yaml.safe_dump(policy)
    if change == "key_order":
        desired_doc["data"]["values.yaml"] = yaml.safe_dump(
            dict(reversed(list(policy.items()))), sort_keys=False
        )
        assert desired_doc != document
    elif change == "type":
        policy["observability"]["enabled"] = 1
        desired_doc["data"]["values.yaml"] = yaml.safe_dump(policy)
    elif change == "list_order":
        document["data"]["values.yaml"] += "ordered: [first, second]\n"
        frozen = generation(document)
        desired_doc["data"]["values.yaml"] += "ordered: [second, first]\n"
    elif change == "extra_data":
        desired_doc["data"]["other.yaml"] = "enabled: true\n"
    elif change == "duplicate":
        desired_doc["data"]["values.yaml"] += "slurmCluster: {enabled: true}\n"
    elif change == "invalid":
        desired_doc["data"]["values.yaml"] = "[unfinished"
    replay = generation(desired_doc)
    replay_calls = []

    def replay_from_frozen(_cli, admitted, _paths, **kwargs):
        assert admitted is frozen
        assert kwargs["render_inputs"] == {"mk8s.cluster_id": "frozen-id"}
        assert kwargs["target_ref"] == "cluster"
        assert kwargs["initialize_terraform"] is False
        replay_calls.append(True)
        return replay

    monkeypatch.setattr(
        deployment_resolution, "resolved_application_generation", replay_from_frozen
    )
    journal = SimpleNamespace(
        payload={"selected": ["cluster"], "targets": {}}, entry=lambda ref: {}
    )
    executor = SimpleNamespace(
        cli=SimpleNamespace(progress_console=Console(file=StringIO(), force_terminal=False)),
        paths=object(),
        generation=frozen,
        _application_journal=lambda: journal,
    )
    before = copy.deepcopy(journal.payload)
    if change in {"same", "key_order"}:
        _CliDeploymentExecutor._preflight_install_application_inputs(executor)
    else:
        with pytest.raises(RuntimeError, match="[Ff]rozen.*bundle"):
            _CliDeploymentExecutor._preflight_install_application_inputs(executor)
    assert replay_calls == [True]
    assert journal.payload == before


@pytest.mark.parametrize("drift", [None, "values", "source"])
def test_execution_refresh_preserves_sealed_oci_bundle(tmp_path, monkeypatch, drift):
    from rich.console import Console

    from nebius_cxcli.compatibility_artifacts import bind_flux_artifacts
    from nebius_cxcli.deployment_cli import _CliDeploymentExecutor
    from nebius_cxcli.soperator_install_render_repair import _file_hashes, _files
    from test_deployment_campaign import paths

    # This fixture isolates OCI publication; effective receipt admission is
    # exercised with the real freeze/admit pair in test_deployment_effective_inputs.
    monkeypatch.setattr(
        "nebius_cxcli.deployment_resolution.bind_generation_compatibility",
        lambda cli, generation, paths: generation,
    )
    local = paths(tmp_path)
    flux = local.flux_dir / "targets/cluster"
    flux.mkdir(parents=True)
    repository = {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "HelmRepository",
        "metadata": {"name": "operator", "namespace": "flux-system"},
        "spec": {"type": "oci", "url": "oci://registry.test/charts"},
    }
    release = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "operator", "namespace": "operators"},
        "spec": {
            "values": {"enabled": True},
            "chart": {
                "spec": {
                    "chart": "operator",
                    "version": "1.0.0",
                    "sourceRef": {
                        "kind": "HelmRepository",
                        "name": "operator",
                        "namespace": "flux-system",
                    },
                }
            },
        },
    }
    inputs = {
        "operator": {
            "reference": {
                "chart_repo": "oci://registry.test/charts",
                "chart_name": "operator",
                "chart_version": "1.0.0",
            },
            "oci_digest": "sha256:" + "a" * 64,
        }
    }

    def raw_render(*args, **kwargs):
        (flux / "repositories.yaml").write_text(yaml.safe_dump(repository, sort_keys=False))
        (flux / "release.yaml").write_text(yaml.safe_dump(release, sort_keys=False))
        (flux / "kustomization.yaml").write_text("resources: [repositories.yaml, release.yaml]\n")

    raw_render()
    bind_flux_artifacts(local, inputs)
    manifest = {
        "deploy": {
            "targets": [
                {"target_ref": "cluster", "flux_dir": flux.relative_to(local.repo_root).as_posix()}
            ]
        },
        "render": {"compatibility": {"chart_inputs": inputs}},
    }
    desired = DeploymentGeneration.capture(local, manifest)
    manifest = desired.manifest_for_paths(local)
    (local.generated_dir / "nebius-cxcli-manifest.json").write_text(json.dumps(manifest))
    sealed = _file_hashes(_files(flux))
    entry = {
        "status": "executing",
        "desiredBundle": target_bundle_digest(desired, "cluster"),
        "inputRepair": {"sealed": True},
    }
    local.reports_dir.mkdir()
    receipt = local.reports_dir / "recovery.json"
    receipt.write_text("preserve recovery state")
    # Reproduce the raw refresh left by an interrupted previous runner.
    raw_render()
    if drift == "values":
        release["spec"]["values"]["enabled"] = False
    elif drift == "source":
        repository["spec"]["url"] = "oci://different.test/charts"
    if drift:
        raw_render()
        desired = DeploymentGeneration.capture(local, manifest)
    runner = SimpleNamespace(
        lease=SimpleNamespace(assert_held=lambda: None),
        paths=local,
        manifest=manifest,
        config={},
        _desired_application_generation=lambda **kw: desired,
        _application_journal=lambda: SimpleNamespace(
            payload={"selected": ["cluster"]}, entry=lambda ref: entry
        ),
        cli=SimpleNamespace(
            _refresh_flux_after_terraform_outputs=lambda *a, **k: pytest.fail(
                "Execution must publish the resolved bundle without another renderer"
            ),
            progress_console=Console(),
        ),
    )
    if drift:
        with pytest.raises(
            (RuntimeError, ValueError),
            match="Application render repair requires an unfinished initial bundle",
        ):
            _CliDeploymentExecutor._prepare_install_application_inputs(runner)
    else:
        _CliDeploymentExecutor._prepare_install_application_inputs(runner)
        assert _file_hashes(_files(flux)) == sealed
    assert receipt.read_text() == "preserve recovery state"
