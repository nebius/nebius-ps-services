from __future__ import annotations

import base64
import json
import os

import pytest

from nebius_cxcli.deployment_recovery import capture_execution_cache, restore_execution_cache
from nebius_cxcli.deployment_retirement import retirement_members
from nebius_cxcli.project_bundle_transaction import (
    ProjectBundleSafetyError,
    ProjectBundleTransaction,
)


def test_recovery_keeps_current_state_without_completed_generation_archives(tmp_path):
    root = tmp_path / "source"
    project = root / "tenant" / "project"
    project.mkdir(parents=True)
    target = project / "config.yaml"
    transaction = ProjectBundleTransaction(project)
    for value in ("first", "second", "current"):
        transaction.commit({target: value})
    generation = transaction.current_generation_sha256()
    archives = set(transaction.generations_dir.iterdir())

    cache = capture_execution_cache(root)
    assert not any("project-bundle-generations" in name for name in cache)
    assert set(transaction.generations_dir.iterdir()) == archives
    restored = tmp_path / "restored"
    restore_execution_cache(restored, cache)
    recovered = ProjectBundleTransaction(restored / "tenant" / "project")
    assert recovered.current_generation_sha256() == generation
    assert (recovered.project_dir / "config.yaml").read_text() == "current"


def test_recovery_preserves_only_pending_generation_and_its_baseline(tmp_path):
    root = tmp_path / "source"
    project = root / "tenant" / "project"
    project.mkdir(parents=True)
    config = project / "config.yaml"
    baseline = project / "generated" / "reports" / "ordinary-apps-baseline.json"
    dashboard = project / "generated" / "grafana_dashboards" / "target" / "example.json"
    ProjectBundleTransaction(project).commit({config: "old"})

    def crash(name):
        if name == "after-commit":
            raise OSError("simulated crash")

    transaction = ProjectBundleTransaction(project, failpoint=crash)
    with pytest.raises(OSError, match="simulated crash"):
        transaction.commit(
            {config: "new", baseline: '{"schema":"nebius-cxcli-ordinary-apps/v1"}', dashboard: "{}"}
        )
    journal = json.loads(transaction.journal_path.read_text())
    pending = transaction.generations_dir / journal["generationId"]
    (pending / "generated" / "reports" / "soperator-unused.json").write_text("{}")
    cache = capture_execution_cache(root)
    assert config.read_text() == "old"
    assert json.loads(transaction.journal_path.read_text())["status"] == "committed"
    staged = {name for name in cache if "project-bundle-generations" in name}
    expected = {
        str((pending / relative).relative_to(root))
        for relative in (
            "config.yaml",
            "generated/reports/ordinary-apps-baseline.json",
            "generated/grafana_dashboards/target/example.json",
        )
    }
    assert staged == expected
    restored = tmp_path / "restored"
    restore_execution_cache(restored, cache)
    recovered = ProjectBundleTransaction(restored / "tenant" / "project")
    assert recovered.recover()
    assert (recovered.project_dir / "config.yaml").read_text() == "new"
    assert (recovered.project_dir / dashboard.relative_to(project)).read_text() == "{}"
    assert (recovered.project_dir / baseline.relative_to(project)).read_bytes() == (
        pending / baseline.relative_to(project)
    ).read_bytes()
    completed = capture_execution_cache(restored)
    assert str(baseline.relative_to(project.parent.parent)) in completed
    assert not any("project-bundle-generations" in name for name in completed)


def test_recovery_capture_rejects_incomplete_pending_generation(tmp_path):
    project = tmp_path / "tenant" / "project"
    project.mkdir(parents=True)

    def crash(name):
        if name == "after-commit":
            raise OSError("simulated crash")

    transaction = ProjectBundleTransaction(project, failpoint=crash)
    with pytest.raises(OSError, match="simulated crash"):
        transaction.commit({project / "config.yaml": "new"})
    journal = json.loads(transaction.journal_path.read_text())
    (transaction.generations_dir / journal["generationId"] / "config.yaml").unlink()
    with pytest.raises(ProjectBundleSafetyError, match="incomplete"):
        capture_execution_cache(tmp_path)


def test_recovery_ignores_uncommitted_staging_without_replaying_it(tmp_path):
    project = tmp_path / "tenant" / "project"
    project.mkdir(parents=True)
    config = project / "config.yaml"
    config.write_text("old")

    def crash(name):
        if name == "after-stage-generation":
            raise OSError("simulated crash")

    transaction = ProjectBundleTransaction(project, failpoint=crash)
    with pytest.raises(OSError, match="simulated crash"):
        transaction.commit({config: "uncommitted"})
    cache = capture_execution_cache(tmp_path)
    assert set(cache) == {"tenant/project/config.yaml"}
    assert base64.b64decode(cache["tenant/project/config.yaml"]) == b"old"
    assert any(transaction.generations_dir.iterdir())


@pytest.mark.parametrize(
    "name",
    [
        "runtime/config.yaml",
        "tenant/project/runtime/config.yaml",
        "tenant/project/generated/infra/credentials.json",
        "tenant/project/generated/infra/terraform.tfstate",
        "tenant/project/generated/infra/.terraform/terraform.tfstate",
        "tenant/project/.nebius-cxcli/project-bundle-generations/runtime-token.txt",
        "tenant/project/generated/flux/token.pem",
        "tenant/project/generated/reports/soperator-install-credentials.json",
        "tenant/project/generated/reports/soperator-install-unknown-repair-cluster.json",
    ],
)
def test_recovery_excludes_and_refuses_runtime_credentials(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    path.write_text("sentinel-private-material")
    assert capture_execution_cache(tmp_path) == {}
    with pytest.raises(RuntimeError, match="forbidden"):
        restore_execution_cache(tmp_path / "new", {name: base64.b64encode(b"secret").decode()})


def test_recovery_roundtrip_and_link_rejection(tmp_path):
    root = tmp_path / "first"
    for name in (
        "tenant/project/config.yaml",
        "tenant/project/generated/infra/main.tf",
        "tenant/project/generated/infra/terraform.auto.tfvars.json",
        "tenant/project/generated/reports/soperator-campaign-topology.json",
        "tenant/project/generated/reports/soperator-install-docker-storage-repair-cluster.json",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    cache = capture_execution_cache(root)
    assert len(cache) == 5
    second = tmp_path / "second"
    restore_execution_cache(second, cache)
    assert capture_execution_cache(second) == cache
    victim = second / "tenant/project/config.yaml"
    victim.unlink()
    victim.symlink_to(root / "tenant/project/config.yaml")
    with pytest.raises(OSError):
        restore_execution_cache(second, cache)
    victim.unlink()
    os.link(root / "tenant/project/config.yaml", victim)
    with pytest.raises(RuntimeError, match="hard link"):
        restore_execution_cache(second, cache)


def node(name="n1", uid="u1", provider_id="group1"):
    return {
        "metadata": {
            "name": name,
            "uid": uid,
            "labels": {
                "nebius.com/node-group-id": provider_id,
                "nebius.com/node-group": "worker",
            },
        }
    }


def test_retirement_requires_complete_exact_membership_not_descriptive_labels():
    kwargs = {"group_ids": {"worker": "group1"}, "counts": {"worker": 2}}
    first, second = node(), node("n2", "u2")
    assert retirement_members([first, second], **kwargs) == {"worker": (("n1", "u1"), ("n2", "u2"))}
    for invalid in (
        [first],
        [first, first],
        [first, node("n2", "")],
        [first, node("n2", "u2", "foreign")],
    ):
        with pytest.raises(RuntimeError):
            retirement_members(invalid, **kwargs)


def test_restore_rejects_symlink_before_creating_any_external_parent(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    (cache_root / "tenant").symlink_to(external, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink"):
        restore_execution_cache(
            cache_root,
            {
                "tenant/project/generated/infra/main.tf": base64.b64encode(
                    b"terraform {}\n"
                ).decode()
            },
        )
    assert list(external.iterdir()) == []


def test_application_journal_rejects_arbitrary_data_on_capture_and_restore(tmp_path):
    import base64
    import json

    from nebius_cxcli.deployment_recovery import capture_execution_cache, restore_execution_cache

    relative = "tenant/project/generated/reports/deployment-applications.json"
    path = tmp_path / "one" / relative
    path.parent.mkdir(parents=True)
    data = json.dumps({"credentials": "sentinel-not-a-real-credential"}).encode()
    path.write_bytes(data)
    with pytest.raises(RuntimeError, match="support-safe"):
        capture_execution_cache(tmp_path / "one")
    with pytest.raises(RuntimeError, match="support-safe"):
        restore_execution_cache(tmp_path / "two", {relative: base64.b64encode(data).decode()})
    assert not (tmp_path / "two" / relative).exists()
