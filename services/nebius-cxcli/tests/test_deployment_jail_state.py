from __future__ import annotations

import base64
import copy
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import cli
from nebius_cxcli.deployment_applications import target_bundle_digest
from nebius_cxcli.deployment_campaign import CampaignGenerationHooks
from nebius_cxcli.deployment_cli import _CliDeploymentExecutor, hydrate_upgrade_jail_config
from nebius_cxcli.deployment_jail_state import (
    ApplicationGenerationJournal,
    accepted_effective_generation,
    build_jail_state_receipt,
    hydrate_accepted_generations,
    jail_values,
    merge_accepted_jail_state,
    validate_application_generations,
)
from nebius_cxcli.deployment_recovery import capture_execution_cache, restore_execution_cache
from nebius_cxcli.deployment_resolution import project_jail_generation
from nebius_cxcli.deployment_state import DeploymentGeneration, DeploymentState, digest
from nebius_cxcli.frozen_catalog import freeze_catalog
from nebius_cxcli.soperator_jail_mounts import apply_jail_persistent_mount_values
from nebius_cxcli.soperator_jail_protection import (
    freeze_jail_protection,
    protect_jail_directories,
    slot_generation,
)
from nebius_cxcli.soperator_rootfs_transition import plan_soperator_rootfs_transition
from test_deployment_campaign import paths
from test_deployment_plan import config
from test_deployment_state import Store, settings
from test_operation_config_authority import _Store
from test_soperator_upstream_adapter import _values, render_soperator_adapter_documents

IDENTITY = {"cluster_id": "cluster-id", "kubernetes_uid": "cluster-uid"}


def initial(layout="managed"):
    values = _values()
    values.pop("jailRootfs", None)
    values.pop("jailPersistentMounts", None)
    values.get("volume", {}).get("jail", {}).pop("localPath", None)
    return apply_jail_persistent_mount_values(
        values, target_ref="cluster", layout=layout, legacy_active_source=False
    )


def switch(values, layout="managed"):
    return plan_soperator_rootfs_transition(
        values, target_ref="cluster", layout=layout, legacy_pvc_resolver=lambda: "legacy"
    )[0]


def bundle(values):
    payload = config()
    payload["apps"]["charts"][0]["values"] = values
    docs, _ = render_soperator_adapter_documents(values)
    return DeploymentGeneration(
        {
            "schema": "nebius-cxcli-generated/v2",
            "execution": {"backend": {}},
            "runtime_config": payload,
            "render": {"inputs": freeze_catalog(payload), "application_inputs": {}},
            "deploy": {
                "targets": [
                    {"target_ref": "cluster", "flux_dir": "flux/targets/cluster"},
                    {"target_ref": "other", "flux_dir": "flux/targets/other"},
                ]
            },
        },
        {
            "flux/targets/cluster/soperator-nebius-adapter.yaml": base64.b64encode(
                yaml.safe_dump_all(docs).encode()
            ).decode(),
            "flux/targets/cluster/kustomization.yaml": base64.b64encode(
                b"resources: [soperator-nebius-adapter.yaml]\n"
            ).decode(),
            "flux/targets/cluster/ordinary/unchanged.yaml": base64.b64encode(
                b"owned: separately"
            ).decode(),
            "flux/targets/other/unchanged.yaml": base64.b64encode(b"other: unchanged").decode(),
        },
    )


def renderer(monkeypatch):
    # Freeze upstream retrieval/compatibility, retaining the real physical-volume renderer.
    monkeypatch.setattr(
        "nebius_cxcli.deployment_resolution.use_generation_soperator_releases",
        lambda *a, **kw: nullcontext(),
    )
    monkeypatch.setattr(
        "nebius_cxcli.compatibility_execution.freeze_compatibility",
        lambda cfg, *args: {
            "report": {"fixture": "pinned"},
            "config_sha256": digest(cli.to_plain_data(cfg)),
        },
    )
    calls = []

    def render(cfg, staged, *, component_output_values):
        calls.append(copy.deepcopy(component_output_values))
        docs, _ = render_soperator_adapter_documents(jail_values(cli.to_plain_data(cfg), "cluster"))
        (staged.flux_dir / "targets/cluster/soperator-nebius-adapter.yaml").write_text(
            yaml.safe_dump_all(docs)
        )

    monkeypatch.setattr(cli, "render_flux", render)
    return calls


def evidence(authored, effective):
    receipt = build_jail_state_receipt(
        authored=authored,
        effective=effective,
        target_ref="cluster",
        identity=IDENTITY,
        storage_evidence={
            "activePvcName": slot_generation(
                jail_values(effective.manifest["runtime_config"], "cluster"),
                jail_values(effective.manifest["runtime_config"], "cluster")["jailRootfs"][
                    "activeSlot"
                ],
            )["pvcName"]
        },
    )
    return {
        "targets": {
            "cluster": {
                "generation": authored.identity,
                "effectiveGeneration": effective.identity,
                "jailState": receipt,
                "identity": IDENTITY,
                "desiredBundle": target_bundle_digest(effective, "cluster"),
            }
        },
        "identities": {"cluster": IDENTITY},
    }


@pytest.mark.parametrize("layout", ["managed", "external"])
def test_two_upgrades_rehydrate_public_intake_and_keep_real_backing(tmp_path, monkeypatch, layout):
    renderer(monkeypatch)
    authored_values = protect_jail_directories(
        initial(layout), paths=["/datasets"], layout=layout, target_ref="cluster"
    )
    authored = bundle(authored_values)
    first_values = switch(authored_values, layout)
    first = project_jail_generation(
        cli,
        authored,
        paths(tmp_path / "first"),
        target_ref="cluster",
        jail_protection=freeze_jail_protection(first_values),
        render_inputs={},
    )
    state = DeploymentState(Store(), settings(), assert_held=lambda: None)
    record = state.begin(authored, plan={})
    state.accept(record, evidence=evidence(authored, first), derived_generations=[first])
    accepted = state.read().value["accepted"]
    assert accepted["generation"] == authored.identity
    assert (
        accepted_effective_generation(state, "cluster", accepted["evidence"]["targets"]["cluster"])
        == first
    )
    source, targets, desired = hydrate_accepted_generations(state, accepted, authored, authored)
    assert source.files == first.files and targets["cluster"] == first
    assert (
        jail_values(desired.manifest["runtime_config"], "cluster")["jailRootfs"]["activeSlot"]
        == "slot-b"
    )

    from nebius_cxcli import deployment_cli

    monkeypatch.setattr(cli, "load_config", lambda *a, **kw: {})
    monkeypatch.setattr(cli, "_ensure_terraform_backend_ready", lambda _: None)
    monkeypatch.setattr(deployment_cli, "backend_settings_from_config", lambda _: settings())
    monkeypatch.setattr(
        "nebius_cxcli.deployment_local.LocalObjectStore.for_project", lambda _: state.store
    )
    intake = hydrate_upgrade_jail_config(
        tmp_path / "config.yaml", authored.manifest["runtime_config"], "cluster"
    )
    second_values = protect_jail_directories(
        jail_values(intake, "cluster"), paths=["/workspace"], layout=layout, target_ref="cluster"
    )
    second_authored = bundle(second_values)
    second_active = switch(second_values, layout)
    second = project_jail_generation(
        cli,
        second_authored,
        paths(tmp_path / "second"),
        target_ref="cluster",
        jail_protection=freeze_jail_protection(second_active),
        render_inputs={},
    )
    old_a = slot_generation(authored_values, "slot-a")
    fresh_a = slot_generation(second_active, "slot-a")
    assert fresh_a != old_a and fresh_a["pvcName"] != old_a["pvcName"]
    mounts = {row["mountPath"]: row["localPath"] for row in second_active["jailPersistentMounts"]}
    assert mounts["/datasets"] == old_a["localPath"] + "/datasets"
    assert (
        mounts["/workspace"] == slot_generation(first_values, "slot-b")["localPath"] + "/workspace"
    )
    record = state.begin(second_authored, plan={})
    state.accept(record, evidence=evidence(second_authored, second), derived_generations=[second])
    assert state.read().value["accepted"]["generation"] == second_authored.identity
    # Rendering really emits both retained PVCs and the fresh active PVC.
    docs = list(
        yaml.safe_load_all(
            base64.b64decode(second.files["flux/targets/cluster/soperator-nebius-adapter.yaml"])
        )
    )
    pvcs = {row["metadata"]["name"] for row in docs if row["kind"] == "PersistentVolumeClaim"}
    assert {
        old_a["pvcName"],
        fresh_a["pvcName"],
        slot_generation(first_values, "slot-b")["pvcName"],
    } <= pvcs
    assert authored == bundle(authored_values)


def test_stale_desired_addition_binds_active_root_and_redirects_fail():
    base = protect_jail_directories(
        initial(), paths=["/datasets"], layout="managed", target_ref="cluster"
    )
    active = switch(base)
    stale = protect_jail_directories(
        base, paths=["/workspace"], layout="managed", target_ref="cluster"
    )
    merged = merge_accepted_jail_state(base, active, stale)
    workspace = next(
        row for row in merged["jailPersistentMounts"] if row["mountPath"] == "/workspace"
    )
    assert workspace["localPath"] == slot_generation(active, "slot-b")["localPath"] + "/workspace"
    for change in ("redirect", "remove", "root", "retained"):
        invalid = copy.deepcopy(base)
        if change == "redirect":
            invalid["jailPersistentMounts"][-1]["pvcName"] = "different-pvc"
        elif change == "remove":
            invalid["jailPersistentMounts"].pop()
        elif change == "root":
            invalid["jailRootfs"]["slots"]["slot-b"]["pvcName"] = "redirect"
        else:
            invalid["jailRootfs"]["retainedGenerations"] = []
        with pytest.raises(ValueError):
            merge_accepted_jail_state(base, active, invalid)


@pytest.mark.parametrize("crash", ["blob", "before-cas", "after-cas"])
def test_effective_blob_precedes_atomic_acceptance_and_lost_ack(tmp_path, crash):
    original = bundle(initial())
    effective = bundle(switch(initial()))
    store = Store()
    state = DeploymentState(store, settings(), assert_held=lambda: None)
    record = state.begin(original, plan={})
    write = store.write

    def interrupted(key, value, *, etag):
        if (crash == "blob" and "/generations/" in key) or (
            crash == "before-cas" and key.endswith("state.json")
        ):
            raise RuntimeError("interrupted")
        result = write(key, value, etag=etag)
        if crash == "after-cas" and key.endswith("state.json"):
            raise RuntimeError("interrupted")
        return result

    store.write = interrupted
    with pytest.raises(RuntimeError, match="interrupted"):
        state.accept(
            record, evidence=evidence(original, effective), derived_generations=[effective]
        )
    store.write = write
    recovered = state.read()
    if recovered.value["accepted"]:
        assert (
            accepted_effective_generation(
                state, "cluster", recovered.value["accepted"]["evidence"]["targets"]["cluster"]
            )
            == effective
        )
    else:
        assert recovered.value["active"]["generation"] == original.identity
        state.accept(
            recovered, evidence=evidence(original, effective), derived_generations=[effective]
        )
    assert state.read().value["active"] is None


def test_grow_and_final_publish_exact_projection_after_cross_runner_lost_ack(tmp_path, monkeypatch):
    calls = renderer(monkeypatch)
    source = bundle(initial())
    active = switch(initial())
    intent = SimpleNamespace(digest=digest("campaign"), target_ref="cluster")
    authority = {"intent": intent.digest, "jailProtection": freeze_jail_protection(active)}
    runner = _CliDeploymentExecutor.__new__(_CliDeploymentExecutor)
    runner.cli, runner.paths = cli, paths(tmp_path / "runner-a")
    runner.campaign_intent = intent
    runner._campaign_jail_authority = lambda **kw: authority
    runner.manifest = source.materialize(runner.paths)
    runner.paths.config_path.write_text(yaml.safe_dump(source.manifest["runtime_config"]))
    hooks = CampaignGenerationHooks()
    hooks.cli, hooks.executor = cli, runner
    hooks.admissions = {
        name: SimpleNamespace(
            application={"generation": source.as_payload(), "generationId": source.identity}
        )
        for name in ("grow", "reconcile")
    }
    store = _Store(fail_first_applied_record=True)
    with pytest.raises(RuntimeError, match="receipt write interruption"):
        hooks._publish("grow", config_store=store, assert_authority=lambda: None)
    cache = capture_execution_cache(runner.paths.repo_root)
    restored = paths(tmp_path / "runner-b")
    restore_execution_cache(restored.repo_root, cache)
    runner.paths = restored
    hooks._reload()
    hooks._publish("grow", config_store=store, assert_authority=lambda: None)
    assert len(calls) == 1  # No rerender on recovery.
    hooks._publish("reconcile", config_store=store, assert_authority=lambda: None)
    assert len(calls) == 2
    written = yaml.safe_load(restored.config_path.read_text())
    assert freeze_jail_protection(jail_values(written, "cluster")) == authority["jailProtection"]
    assert (restored.flux_dir / "targets/other/unchanged.yaml").read_text() == "other: unchanged"
    assert (
        restored.flux_dir / "targets/cluster/ordinary/unchanged.yaml"
    ).read_text() == "owned: separately"
    runner.generation = runner.desired_generation = source
    runner.hooks = hooks
    runner.lease = SimpleNamespace(assert_held=lambda: None)
    runner._campaign_complete = True
    expected = runner._desired_application_generation(fresh=True)
    assert expected.files == DeploymentGeneration.capture(restored, runner.manifest).files
    assert len(calls) == 2
    assert expected.manifest["render"]["compatibility"]["config_sha256"] == digest(
        cli.to_plain_data(cli.runtime_config_from_manifest(expected.manifest))
    )


def test_sealed_generation_rejects_binding_change_and_missing_required_bytes(tmp_path):
    journal = ApplicationGenerationJournal(tmp_path / "journal.json", digest("campaign"))
    binding = {
        "generation": digest("authored"),
        "authority": {
            "intent": digest("campaign"),
            "jailProtection": freeze_jail_protection(initial()),
        },
    }
    frozen = bundle(initial())
    journal.seal("grow", binding=binding, build=lambda: frozen, assert_authority=lambda: None)
    changed = {**binding, "generation": digest("different")}
    with pytest.raises(RuntimeError, match="binding changed"):
        journal.seal(
            "grow",
            binding=changed,
            build=lambda: pytest.fail("rebuild"),
            assert_authority=lambda: None,
        )
    with pytest.raises(RuntimeError, match="missing"):
        journal.seal(
            "reconcile",
            binding=binding,
            build=lambda: pytest.fail("rebuild"),
            assert_authority=lambda: None,
            required=True,
        )
    value = journal._load()
    value["entries"]["grow"]["generation"]["files"] = {}
    with pytest.raises(RuntimeError, match="identity"):
        validate_application_generations(value)


def test_later_acceptance_cannot_drop_existing_jail_authority():
    original, effective = bundle(initial()), bundle(switch(initial()))
    state = DeploymentState(Store(), settings(), assert_held=lambda: None)
    record = state.begin(original, plan={})
    state.accept(record, evidence=evidence(original, effective), derived_generations=[effective])
    prior = state.read().value["accepted"]
    record = state.begin(original, plan={})
    with pytest.raises(RuntimeError, match="cannot omit"):
        state.accept(
            record,
            evidence={"targets": {"cluster": {"generation": original.identity, "ready": True}}},
        )
    assert state.read().value["accepted"] == prior


@pytest.mark.parametrize("nfs", [False, True])
def test_fresh_physical_admission_accepts_local_and_nfs_backing_and_rejects_redirects(
    tmp_path, nfs
):
    import json

    from nebius_cxcli.deployment_jail_state import observe_jail_storage

    values = initial()
    if nfs:
        values["externalNfs"] = {
            "enabled": True,
            "server": "192.0.2.2",
            "path": "/home",
            "mountPath": "/home",
        }
    docs, adapter = render_soperator_adapter_documents(values)
    objects = {}
    for document in docs:
        if document["kind"] not in {"PersistentVolume", "PersistentVolumeClaim"}:
            continue
        row = copy.deepcopy(document)
        row["metadata"]["uid"] = "uid-" + row["metadata"]["name"]
        objects[("pv" if row["kind"] == "PersistentVolume" else "pvc", row["metadata"]["name"])] = (
            row
        )
    for (kind, name), row in objects.items():
        if kind == "pvc":
            pv = objects[("pv", row["spec"]["volumeName"])]
            pv["spec"]["claimRef"] = {
                "name": name,
                "namespace": "soperator",
                "uid": row["metadata"]["uid"],
            }
    fake = SimpleNamespace(
        _rendered_soperator_adapter_state=lambda _: adapter,
        _soperator_upgrade_live_slurmcluster_namespaces=lambda **kw: ["soperator"],
        _run_soperator_upgrade_kubectl=lambda ns, args, **kw: SimpleNamespace(
            stdout=json.dumps(objects[(args[1], args[2])])
        ),
        _live_soperator_jail_pvc_for_reconcile=lambda **kw: adapter["activePvc"],
    )
    proof = observe_jail_storage(
        fake, flux_dir=tmp_path, values=values, kube_context="ctx", kube_env={}
    )
    assert proof["activePvcName"] == adapter["activePvc"]
    changed = "external-nfs-home-pv" if nfs else adapter["slots"]["slot-a"]["pv_name"]
    objects[("pv", changed)]["spec"]["nfs" if nfs else "local"]["path"] = "/redirect"
    with pytest.raises(RuntimeError, match="physical backing"):
        observe_jail_storage(
            fake, flux_dir=tmp_path, values=values, kube_context="ctx", kube_env={}
        )


def test_same_release_selection_preserves_slot_and_seals_final_bytes(tmp_path, monkeypatch):
    renderer(monkeypatch)
    values = protect_jail_directories(
        initial(), paths=["/datasets"], layout="managed", target_ref="cluster"
    )
    frozen = bundle(values)
    runner = _CliDeploymentExecutor.__new__(_CliDeploymentExecutor)
    runner.cli, runner.paths = cli, paths(tmp_path)
    runner.campaign_intent = SimpleNamespace(
        digest=digest("same-release"),
        target_ref="cluster",
        segments=(),
        jail_protection=freeze_jail_protection(values),
    )
    runner.generation = runner.desired_generation = frozen
    runner.hooks = SimpleNamespace(
        admissions={
            "reconcile": SimpleNamespace(
                application={"generation": frozen.as_payload(), "generationId": frozen.identity}
            )
        }
    )
    runner.lease = SimpleNamespace(assert_held=lambda: None)
    runner._campaign_complete = False
    result = runner._desired_application_generation()
    runner._campaign_complete = True
    assert runner._desired_application_generation(fresh=True) == result
    assert (
        jail_values(result.manifest["runtime_config"], "cluster")["jailRootfs"]["activeSlot"]
        == "slot-a"
    )


def test_sealing_remote_ack_failure_prevents_application_publication(tmp_path, monkeypatch):
    from nebius_cxcli.deployment_recovery import execution_checkpoint

    renderer(monkeypatch)
    frozen = bundle(initial())
    runner = _CliDeploymentExecutor.__new__(_CliDeploymentExecutor)
    runner.cli, runner.paths = cli, paths(tmp_path)
    runner.campaign_intent = SimpleNamespace(digest=digest("campaign"), target_ref="cluster")
    runner._campaign_jail_authority = lambda **kw: {
        "intent": digest("campaign"),
        "jailProtection": freeze_jail_protection(switch(initial())),
    }
    runner.manifest = frozen.materialize(runner.paths)
    original = yaml.safe_dump(frozen.manifest["runtime_config"])
    runner.paths.config_path.write_text(original)
    hook = CampaignGenerationHooks()
    hook.cli, hook.executor = cli, runner
    hook.admissions = {
        "grow": SimpleNamespace(
            application={"generation": frozen.as_payload(), "generationId": frozen.identity}
        )
    }
    store = _Store()

    def fail_ack():
        raise RuntimeError("remote checkpoint unacknowledged")

    with execution_checkpoint(fail_ack), pytest.raises(RuntimeError, match="unacknowledged"):
        hook._publish("grow", config_store=store, assert_authority=lambda: None)
    assert runner.paths.config_path.read_text() == original
    assert not store.transitions


@pytest.mark.parametrize("field", ["pvcUid", "pvUid", "backing"])
def test_final_handoff_rejects_replaced_protected_volume_with_same_name(field):
    from nebius_cxcli.deployment_jail_state import verify_storage_handoff

    previous = {
        "volumes": {
            "protected": {
                "pvcUid": "pvc-original",
                "pvUid": "pv-original",
                "backing": {"local": {"path": "/same"}},
            }
        },
        "directoryIdentities": [],
    }
    current = copy.deepcopy(previous)
    current["volumes"]["protected"][field] = "replacement"
    with pytest.raises(RuntimeError, match="protected PV/PVC identity"):
        verify_storage_handoff(previous=previous, current=current, release={})


def test_only_exact_materialized_disposable_target_can_replace_previous_volume_identity():
    from nebius_cxcli.deployment_jail_state import verify_storage_handoff

    previous = {"volumes": {"passive": {"pvcUid": "old-pvc", "pvUid": "old-pv"}}}
    current = {
        "activePvcName": "passive",
        "volumes": {"passive": {"pvcUid": "new-pvc", "pvUid": "new-pv"}},
    }
    release = {
        "materialization": {
            "targetPvcName": "passive",
            "targetPvcUid": "new-pvc",
            "targetPvUid": "new-pv",
        }
    }
    verify_storage_handoff(previous=previous, current=current, release=release)
    current["volumes"]["passive"]["pvUid"] = "replaced-after-child"
    with pytest.raises(RuntimeError, match="completed release child"):
        verify_storage_handoff(previous=previous, current=current, release=release)


def test_projection_compatibility_describes_preserved_other_target_bytes(tmp_path, monkeypatch):
    renderer(monkeypatch)
    original_render = cli.render_flux

    def render(cfg, staged, **kwargs):
        original_render(cfg, staged, **kwargs)
        (staged.flux_dir / "targets/other/unchanged.yaml").write_text("unselected rerender")

    monkeypatch.setattr(cli, "render_flux", render)

    def freeze(cfg, staged):
        return {
            "report": {"otherBytes": (staged.flux_dir / "targets/other/unchanged.yaml").read_text()}
        }

    monkeypatch.setattr("nebius_cxcli.compatibility_execution.freeze_compatibility", freeze)
    result = project_jail_generation(
        cli,
        bundle(initial()),
        paths(tmp_path),
        target_ref="cluster",
        jail_protection=freeze_jail_protection(switch(initial())),
        render_inputs={},
    )
    assert result.manifest["render"]["compatibility"]["report"]["otherBytes"] == "other: unchanged"


def test_recovery_reuses_source_compatibility_seal_across_evaluation_dates(tmp_path, monkeypatch):
    from dataclasses import asdict, replace

    from nebius_cxcli.deployment_plan import DeploymentStage, DeploymentStageKind
    from nebius_cxcli.deployment_workflow import StageAdmission
    from test_soperator_full_stack_upgrade import _intent

    old = bundle(initial())
    frozen_compatibility = {"report": {"evaluated_on": "2026-09-17"}}
    sealed = replace(
        old,
        manifest={
            **old.manifest,
            "render": {**old.manifest["render"], "compatibility": frozen_compatibility},
        },
    )
    changed = replace(
        old,
        manifest={
            **old.manifest,
            "render": {
                **old.manifest["render"],
                "compatibility": {"report": {"evaluated_on": "2026-09-18"}},
            },
        },
    )
    runner = _CliDeploymentExecutor.__new__(_CliDeploymentExecutor)
    runner.source_generation, runner.generation = changed, old

    def assess(source, desired, intent):
        assert source["render"]["compatibility"] == frozen_compatibility
        return {"frozen": True}

    monkeypatch.setattr("nebius_cxcli.compatibility_transitions.assess_upgrade_states", assess)
    runner.restore_admissions(
        [
            StageAdmission(
                DeploymentStage(DeploymentStageKind.TRANSITION, old.manifest["runtime_config"]),
                {},
                {
                    "campaign": asdict(_intent()),
                    "sourceCompatibility": frozen_compatibility,
                    "sourceGenerationId": sealed.identity,
                    "compatibility": {"frozen": True},
                },
            )
        ]
    )
    assert runner.source_generation == sealed
