from __future__ import annotations

import copy
import json
from contextlib import ExitStack
from types import SimpleNamespace

import pytest

from nebius_vpngw import apply_approval, cli, runtime_migration
from nebius_vpngw.deploy import ordinary_apply
from nebius_vpngw.deploy.apply_command import CommandJournal


@pytest.mark.parametrize("phase", ["inhibited", "packages", "activating"])
def test_ordinary_cli_reuses_original_approval_after_partial_effects(monkeypatch, phase, tmp_path):
    journal = CommandJournal("project", "gateway", root=tmp_path / "command")
    instance = SimpleNamespace(hostname="gateway")
    manager = SimpleNamespace(inspect_ordinary_targets=lambda _: {"gateway": ("compute", "target")})
    item = SimpleNamespace(
        noop=False,
        effects=["upgrade"],
        digest="1" * 64,
        observation={"runtime_pending": None},
        manifest={"wheel_sha256": "a" * 64},
        close=lambda: None,
    )
    monkeypatch.setattr(ordinary_apply, "inspect_plan", lambda *args, **kwargs: item)
    approval = []
    original = cli._approve_disruption
    monkeypatch.setattr(
        cli, "_approve_disruption", lambda value, **kwargs: approval.append(value) or True
    )
    with ExitStack() as lifetimes:
        token = cli._VM_MANAGER_LIFETIMES.set(lifetimes)
        try:
            first = cli._prepare_ordinary_apply(
                manager,
                None,
                (instance,),
                {"desired": "same"},
                supplied=None,
                dry_run=False,
                recreate=False,
                command_journal=journal,
                artifact_sha256="a" * 64,
            )
            assert item.manifest["command_approval"]["approval"] == approval[0]
            with journal:
                first[3].begin()
                journal.started("guest:gateway")
            # A restarted CLI sees durable guest metadata and changed current
            # observations; the original command token must still be admitted.
            item.manifest = copy.deepcopy(item.manifest)
            item.observation = {"runtime_pending": {"phase": phase, "operation": "e" * 32}}
            item.digest = "2" * 64
            monkeypatch.setattr(cli, "_approve_disruption", original)
            result = cli._prepare_ordinary_apply(
                manager,
                None,
                (instance,),
                {"desired": "same"},
                supplied=approval[0],
                dry_run=False,
                recreate=False,
                command_journal=journal,
                artifact_sha256="a" * 64,
            )
            assert result[2] is True
            with pytest.raises(RuntimeError, match="reviewed successor"):
                cli._prepare_ordinary_apply(
                    manager,
                    None,
                    (instance,),
                    {"desired": "changed"},
                    supplied=approval[0],
                    dry_run=False,
                    recreate=False,
                    command_journal=journal,
                    artifact_sha256="a" * 64,
                )
        finally:
            cli._VM_MANAGER_LIFETIMES.reset(token)


def test_ha_runtime_release_does_not_complete_parent_authority(monkeypatch):
    value = {
        "phase": "complete",
        "operation": "a" * 32,
        "manifest": {"ha_parent": "exact-pair-operation"},
        "plan": {"closure": "approved"},
    }
    monkeypatch.setattr(runtime_migration, "state", lambda: value)
    assert runtime_migration.pending("exact-pair-operation")["manifest"] == value["manifest"]
    assert runtime_migration.plan("exact-pair-operation") == value["plan"]
    assert runtime_migration.pending("another-operation") is None


def test_ha_prepare_reuses_released_suboperation_before_parent_completion(
    monkeypatch, tmp_path, runtime_migration_guest
):
    from nebius_vpngw.deploy import handoff_remote as handoff

    approved = runtime_migration.plan()
    manifest = {"runtime_plan": approved, "ha_parent": "exact-parent"}
    directory = runtime_migration.stage(approved) / "payload"
    runtime_migration.write(directory / "manifest.json", json.dumps(manifest).encode())
    runtime_migration.begin(approved, "a" * 32, manifest)
    value = runtime_migration.state()
    value["phase"] = "complete"
    runtime_migration.save(value)
    monkeypatch.setattr(handoff.remote, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(handoff, "MARKER", tmp_path / "no-marker")
    monkeypatch.setattr(handoff.repair, "STATE", tmp_path / "ha")
    monkeypatch.setattr(handoff, "package_observation", lambda _: {"current": "new-runtime"})
    monkeypatch.setattr(
        handoff.ops, "Journal", lambda: SimpleNamespace(read=lambda: {"state": "complete"})
    )
    monkeypatch.setattr(
        runtime_migration,
        "packages",
        lambda: pytest.fail(
            "must reuse original operation before old package predecessor comparison"
        ),
    )

    class ReachedExistingOperation(Exception):
        pass

    def next_effect(**kwargs):
        assert runtime_migration.state()["operation"] == "a" * 32
        raise ReachedExistingOperation

    monkeypatch.setattr(handoff.ops, "install_startup_guard", next_effect)
    with pytest.raises(ReachedExistingOperation):
        handoff.prepare_guarded(
            {"package": {"desired": manifest, "predecessor": {"current": "old-runtime"}}}
        )


@pytest.mark.parametrize(
    "field", ["target", "transport", "manifest", "desired", "artifact", "intent"]
)
def test_ha_resumed_approval_binds_exact_members_and_package_closure(field):
    package = SimpleNamespace(
        target_identity="compute", target="target", manifest={"runtime_plan": {"install": "exact"}}
    )
    artifact = SimpleNamespace(sha256="a" * 64, dependency_plans=(("gateway", package),))
    scope = cli._vm_ha_command_approval_scope(artifact, {"config": "same"})
    record = apply_approval.bind("b" * 64, scope)
    package.manifest["ha_parent"] = "pair-operation"
    assert (
        apply_approval.resume(
            record, cli._vm_ha_command_approval_scope(artifact, {"config": "same"})
        )
        == "b" * 64
    )
    changed = copy.deepcopy(scope)
    if field in {"desired", "artifact", "intent"}:
        changed[field] = "different"
    else:
        changed["packages"]["gateway"][field] = "different"
    with pytest.raises(RuntimeError, match="authority changed"):
        apply_approval.resume(record, changed)


def test_attachment_only_ordinary_change_requires_approval_with_no_guest_effects(
    monkeypatch, tmp_path
):
    journal = CommandJournal("project", "gateway", root=tmp_path / "command")
    instance = SimpleNamespace(hostname="gateway")
    manager = SimpleNamespace(inspect_ordinary_targets=lambda _: {"gateway": ("compute", "target")})
    item = SimpleNamespace(
        noop=True,
        effects=[],
        digest="1" * 64,
        observation={"runtime_pending": None},
        manifest={"wheel_sha256": "a" * 64},
        close=lambda: None,
    )
    monkeypatch.setattr(ordinary_apply, "inspect_plan", lambda *args, **kwargs: item)
    approved = []
    monkeypatch.setattr(
        cli,
        "_approve_disruption",
        lambda value, **kwargs: approved.append((value, kwargs)) or not kwargs["dry_run"],
    )
    attachments = {
        "desired_ids": [],
        "members": {
            "gateway": {"before": [{"explicit_ids": ["sg-old"]}], "desired": [{"explicit_ids": []}]}
        },
    }
    with ExitStack() as stack:
        token = cli._VM_MANAGER_LIFETIMES.set(stack)
        try:
            _, _, result, _ = cli._prepare_ordinary_apply(
                manager,
                None,
                (instance,),
                {},
                supplied=None,
                dry_run=True,
                recreate=False,
                command_journal=journal,
                artifact_sha256="a" * 64,
                attachments=attachments,
            )
            assert not result and "command_approval" not in item.manifest
            _, _, result, _ = cli._prepare_ordinary_apply(
                manager,
                None,
                (instance,),
                {},
                supplied=None,
                dry_run=False,
                recreate=False,
                command_journal=journal,
                artifact_sha256="a" * 64,
                attachments=attachments,
            )
            assert result and approved[0][0] == approved[1][0]
            assert item.manifest["command_approval"]["approval"] == approved[0][0]
        finally:
            cli._VM_MANAGER_LIFETIMES.reset(token)


def test_pending_attachment_approval_cannot_authorize_new_guest_effects(monkeypatch, tmp_path):
    journal = CommandJournal("project", "gateway", root=tmp_path / "command")
    instance = SimpleNamespace(hostname="gateway")
    manager = SimpleNamespace(inspect_ordinary_targets=lambda _: {"gateway": ("compute", "target")})
    item = SimpleNamespace(
        noop=False,
        effects=["repair guest files and restart services"],
        digest="1" * 64,
        observation={"runtime_pending": None},
        manifest={"wheel_sha256": "a" * 64},
        close=lambda: None,
    )
    monkeypatch.setattr(ordinary_apply, "inspect_plan", lambda *args, **kwargs: item)
    monkeypatch.setattr(
        cli,
        "_approve_disruption",
        lambda *args, **kwargs: pytest.fail("pending authority cannot become a fresh approval"),
    )
    attachments = {
        "desired_ids": [],
        "members": {
            "gateway": {"before": [{"explicit_ids": ["sg-old"]}], "desired": [{"explicit_ids": []}]}
        },
    }
    with ExitStack() as stack:
        token = cli._VM_MANAGER_LIFETIMES.set(stack)
        try:
            with pytest.raises(RuntimeError, match="not bound to this apply command"):
                cli._prepare_ordinary_apply(
                    manager,
                    None,
                    (instance,),
                    {},
                    supplied=None,
                    dry_run=False,
                    recreate=False,
                    command_journal=journal,
                    artifact_sha256="a" * 64,
                    attachments=attachments,
                    attachment_pending={"approval": "b" * 64},
                )
            assert "command_approval" not in item.manifest
        finally:
            cli._VM_MANAGER_LIFETIMES.reset(token)
