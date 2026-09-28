from __future__ import annotations

import base64
import copy
import json
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import cli, flux_render
from nebius_cxcli.deployment_applications import ApplicationJournal, target_bundle_digest
from nebius_cxcli.deployment_observation import target_documents, verify_desired_target
from nebius_cxcli.deployment_resolution import resolved_application_generation
from nebius_cxcli.deployment_state import DeploymentGeneration
from nebius_cxcli.frozen_catalog import freeze_catalog
from test_deployment_campaign import paths
from test_deployment_plan import config


@pytest.fixture
def frozen_release_verifier(monkeypatch):
    from nebius_cxcli import soperator_release_artifacts, soperator_release_resolver

    observed = []

    def hydrate(snapshot):
        return SimpleNamespace(snapshot=snapshot, source=object())

    monkeypatch.setattr(
        soperator_release_resolver, "frozen_soperator_release_from_snapshot", hydrate
    )
    monkeypatch.setattr(cli, "frozen_soperator_release_from_snapshot", hydrate)
    monkeypatch.setattr(
        soperator_release_artifacts,
        "verify_soperator_release_artifacts",
        lambda snapshot, *a, **kw: observed.append(snapshot),
    )
    return observed


def bind_snapshot(payload, files, snapshot, *, target="cluster"):
    for row in payload["apps"]["charts"]:
        if row["id"] == "soperator" and row["instance_id"] == target:
            row.update(version=snapshot.release, repo=snapshot.chart_oci_url("umbrella"))
    files[f"reports/soperator-release-snapshot-{target}.json"] = base64.b64encode(
        json.dumps(snapshot.canonical_payload()).encode()
    ).decode()


@pytest.mark.parametrize("target_ref", ["", "cluster"])
@pytest.mark.parametrize("render_fails", [False, True])
def test_application_render_reuses_every_generation_release(
    tmp_path, monkeypatch, frozen_release_verifier, target_ref, render_fails
):
    from nebius_cxcli.soperator_release_resolver import current_frozen_soperator_release
    from soperator_fixtures import sample_snapshot

    payload, files = config(), {}
    second = copy.deepcopy(payload["apps"]["charts"][0])
    second["instance_id"] = "second"
    payload["apps"]["charts"].append(second)
    snapshots = [
        sample_snapshot(release=version, target_ref=target)
        for target, version in (("cluster", "4.1.9"), ("second", "4.1.11"))
    ]
    for target, snapshot in zip(("cluster", "second"), snapshots, strict=True):
        bind_snapshot(payload, files, snapshot, target=target)
    frozen = DeploymentGeneration(
        {"runtime_config": payload, "render": {"inputs": freeze_catalog(payload)}}, files
    )
    before = copy.deepcopy(frozen)

    def moved_tag(*args, **kwargs):
        raise ValueError("official OCI chart helm-nfs-server differs from release source")

    monkeypatch.setattr(flux_render, "freeze_soperator_release", moved_tag)
    monkeypatch.setattr(
        "nebius_cxcli.compatibility_execution.freeze_compatibility", lambda *a: {"report": {}}
    )

    def render(cfg, staged, **kwargs):
        for snapshot in snapshots:
            bound = current_frozen_soperator_release(
                snapshot.release, target_ref=snapshot.target_ref
            )
            # Exercise the same discovery fallback used by the real Flux renderer.
            if bound is None:
                bound = flux_render.freeze_soperator_release(snapshot.release)
            assert bound.snapshot == snapshot
        if render_fails:
            raise RuntimeError("render interrupted")

    monkeypatch.setattr(cli, "render_flux", render)

    def resolve():
        return resolved_application_generation(
            cli, frozen, paths(tmp_path), target_ref=target_ref, render_inputs={}
        )

    if render_fails:
        with pytest.raises(RuntimeError, match="render interrupted"):
            resolve()
    else:
        result = resolve()
        assert result.files == files
    assert frozen_release_verifier == snapshots
    assert frozen == before
    assert all(
        current_frozen_soperator_release(s.release, target_ref=s.target_ref) is None
        for s in snapshots
    )


def test_application_output_resolver_includes_native_nfs_mount_options(tmp_path, monkeypatch):
    from nebius_cxcli.deployment_resolution import resolve_application_outputs

    payload = config()
    payload["infra"]["components"].append(
        {
            "id": "nfs",
            "instance_id": "nfs",
            "enabled": True,
            "inputs": {"kubernetes_target_ref": "cluster"},
        }
    )
    captured = []
    outputs = {
        "nfs.server_ip": "192.0.2.20",
        "nfs.export_path": "/data",
        "nfs.mount_options": ["nfsvers=4.1"],
    }

    def read_outputs(_config, _paths, *, required_specs, initialize_terraform):
        captured.extend(required_specs)
        return outputs

    monkeypatch.setattr(cli, "_runtime_component_output_values", read_outputs)
    actual = resolve_application_outputs(
        cli, payload, paths(tmp_path), include_soperator_handoffs=False
    )
    assert actual == outputs
    assert {spec["source_ref"] for spec in captured} >= set(outputs)
    assert any(row["id"] == "csi-driver-nfs" for row in payload["apps"]["charts"])


def test_output_resolution_is_derived_from_frozen_inputs_and_repeats_across_runners(
    tmp_path, monkeypatch, frozen_release_verifier
):
    from soperator_fixtures import sample_snapshot

    payload = config()
    monkeypatch.setattr(
        "nebius_cxcli.compatibility_execution.freeze_compatibility",
        lambda *a: {"report": {"fixture": "constraints-adapter"}},
    )
    payload["infra"]["components"].append(
        {
            "id": "nfs",
            "instance_id": "nfs",
            "enabled": True,
            "inputs": {"kubernetes_target_ref": "cluster"},
        }
    )
    document = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "cluster", "namespace": "flux-system"},
        "spec": {"values": {"externalNfs": {}}},
    }
    frozen = DeploymentGeneration(
        {
            "runtime_config": payload,
            "render": {"inputs": freeze_catalog(payload)},
            "deploy": {"targets": [{"target_ref": "cluster", "flux_dir": "flux/targets/cluster"}]},
        },
        {
            "flux/targets/cluster/kustomization.yaml": base64.b64encode(
                b"resources: [release.yaml]"
            ).decode(),
            "flux/targets/cluster/release.yaml": base64.b64encode(
                yaml.safe_dump(document).encode()
            ).decode(),
        },
    )
    bind_snapshot(payload, frozen.files, sample_snapshot(target_ref="cluster"))
    outputs = {"nfs.server_ip": "192.0.2.20", "nfs.export_path": "/data"}
    reads = []
    monkeypatch.setattr(
        cli,
        "_runtime_component_output_values",
        lambda cfg, p, **kw: reads.append(p.infra_dir) or copy.deepcopy(outputs),
    )

    # Preserve the real NFS output materializer, isolate upstream chart retrieval.
    def render(cfg, staged, *, component_output_values):
        chart = {"values": {"externalNfs": {}}}
        flux_render._materialize_soperator_nfs_values(
            payload=cli.to_plain_data(cfg),
            chart_node=chart,
            target_ref="cluster",
            resolved_component_outputs=component_output_values,
        )
        resolved = copy.deepcopy(document)
        resolved["spec"]["values"] = chart["values"]
        (staged.flux_dir / "targets/cluster/release.yaml").write_text(yaml.safe_dump(resolved))

    monkeypatch.setattr(cli, "render_flux", render)
    first = paths(tmp_path / "runner-a")
    second = paths(tmp_path / "runner-b")
    desired = resolved_application_generation(cli, frozen, first)
    repeated = resolved_application_generation(cli, frozen, second)
    assert desired == repeated and desired.identity != frozen.identity
    assert reads == [first.infra_dir, second.infra_dir]

    # Frozen replay must remain available before Terraform has produced outputs.
    def unavailable_outputs(*args, **kwargs):
        pytest.fail("frozen replay attempted to read current Terraform outputs")

    with monkeypatch.context() as frozen_replay:
        frozen_replay.setattr(cli, "_runtime_component_output_values", unavailable_outputs)
        replay = resolved_application_generation(
            cli, frozen, second, initialize_terraform=False, render_inputs=outputs
        )
    assert replay == desired
    live = next(iter(target_documents(desired, "cluster").values()))
    assert live["spec"]["values"]["externalNfs"]["server"] == "192.0.2.20"
    live["metadata"].update(uid="u", generation=1)
    live["status"] = {"observedGeneration": 1, "conditions": [{"type": "Ready", "status": "True"}]}
    fake = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _run_soperator_upgrade_kubectl=lambda *a, **kw: SimpleNamespace(stdout=json.dumps(live)),
    )
    assert verify_desired_target(fake, generation=desired, target_ref="cluster", kube_env={})[
        "ready"
    ]
    with pytest.raises(RuntimeError, match="settings have not converged"):
        verify_desired_target(fake, generation=frozen, target_ref="cluster", kube_env={})
    journal = ApplicationJournal(
        tmp_path / "journal.json", generation=frozen.identity, selected=["cluster"]
    )
    identity = {"cluster_id": "cluster-id", "kubernetes_uid": "cluster-uid"}
    journal.bind("cluster", identity, target_bundle_digest(desired, "cluster"))
    outputs["nfs.server_ip"] = "192.0.2.21"
    changed = resolved_application_generation(cli, frozen, second)
    with pytest.raises(RuntimeError, match="checkpointed identity or bundle"):
        journal.bind("cluster", identity, target_bundle_digest(changed, "cluster"))
    assert next(iter(target_documents(frozen, "cluster").values()))["spec"]["values"] == {
        "externalNfs": {}
    }


@pytest.mark.parametrize("initialize", [False, True])
def test_runtime_output_lookup_initializes_only_when_requested(tmp_path, monkeypatch, initialize):
    reads = []
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _: {})
    monkeypatch.setattr(
        cli,
        "terraform_output_json",
        lambda *a, **kw: (
            reads.append(kw["initialize"])
            or {
                "nfs_server_ip": {"value": "192.0.2.20"},
            }
        ),
    )
    result = cli._runtime_component_output_values(
        {},
        paths(tmp_path),
        initialize_terraform=initialize,
        required_specs=[
            {"component_id": "nfs", "output_name": "server_ip", "source_ref": "nfs.server_ip"}
        ],
    )
    assert result == {"nfs.server_ip": "192.0.2.20"}
    assert reads == [initialize]


def test_sensitive_output_is_rejected_before_application_input_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _: {})
    monkeypatch.setattr(
        cli,
        "terraform_output_json",
        lambda *a, **kw: {"nfs_server_ip": {"sensitive": True, "value": "fixture-private-value"}},
    )
    with pytest.raises(RuntimeError, match="Sensitive Terraform outputs") as error:
        cli._runtime_component_output_values(
            {},
            paths(tmp_path),
            required_specs=[
                {"component_id": "nfs", "output_name": "server_ip", "source_ref": "nfs.server_ip"}
            ],
        )
    assert "fixture-private-value" not in str(error.value)
