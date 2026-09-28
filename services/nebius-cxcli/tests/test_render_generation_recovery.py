"""Completed render publications retain their admitted tombstone authority."""

import hashlib
from dataclasses import replace

import pytest

from nebius_cxcli.paths import resolve_project_paths
from nebius_cxcli.project_bundle_transaction import ProjectBundleTransaction
from nebius_cxcli.render import (
    build_project_generation_plan,
    completed_render_generation_matches,
    project_generation_plan_fingerprints,
    staged_generated_paths,
)


@pytest.mark.parametrize("deleted_name", ["obsolete.json", "compatibility-admission.json"])
def test_completed_render_recovery_preserves_original_authority(tmp_path, deleted_name):
    config = tmp_path / "config.yaml"
    config.write_text("old: true\n")
    paths = resolve_project_paths(config)
    paths.reports_dir.mkdir(parents=True)
    deleted = paths.reports_dir / deleted_name
    deleted.write_text("{}\n")
    staged = staged_generated_paths(paths)
    staged.infra_dir.mkdir(parents=True)
    (staged.infra_dir / "main.tf").write_text("terraform {}\n")
    plan = build_project_generation_plan(
        final_paths=paths, staged_paths=staged, config_path=config, config_content="new: true\n"
    )
    # Model the exact admitted tombstone, including a receipt once misclassified
    # as render-owned. Preserve its original admission and transaction digests.
    removals = (deleted,)
    preimages = {
        **plan.expected_preimages,
        deleted: "sha256:" + hashlib.sha256(deleted.read_bytes()).hexdigest(),
    }
    sha, preimage_sha = project_generation_plan_fingerprints(
        project_dir=paths.project_dir,
        writes=plan.writes,
        removals=removals,
        expected_preimages=preimages,
    )
    transaction = ProjectBundleTransaction(paths.project_dir)
    transaction.commit(
        plan.writes, removals=removals, expected_preimages=preimages, generation_sha256=sha
    )
    original_journal = transaction.journal_path.read_bytes()
    if deleted_name == "compatibility-admission.json":
        deleted.write_text('{"admitted": true}\n')
        assert transaction.current_generation_sha256() is None
    desired = build_project_generation_plan(
        final_paths=paths, staged_paths=staged, config_path=config, config_content="new: true\n"
    )
    assert desired.sha256 != sha
    kwargs = dict(
        paths=paths, desired=desired, admitted_sha256=sha, admitted_preimage_sha256=preimage_sha
    )
    assert completed_render_generation_matches(**kwargs)
    assert transaction.journal_path.read_bytes() == original_journal
    assert not completed_render_generation_matches(
        **{**kwargs, "admitted_preimage_sha256": "sha256:" + "0" * 64}
    )
    assert not completed_render_generation_matches(**{**kwargs, "admitted_sha256": desired.sha256})
    changed = replace(desired, writes={**desired.writes, config: b"different: true\n"})
    assert not completed_render_generation_matches(**{**kwargs, "desired": changed})
    extra = paths.reports_dir / "unadmitted.json"
    extra.write_text("{}")
    assert not completed_render_generation_matches(
        **{**kwargs, "desired": replace(desired, removals=(extra,))}
    )
    if deleted_name == "compatibility-admission.json":
        deleted.unlink()
        deleted.symlink_to(config)
        with pytest.raises((RuntimeError, ValueError), match="symlink|unsafe"):
            completed_render_generation_matches(**kwargs)
        deleted.unlink()
        deleted.write_text("{}")
    else:
        deleted.write_text("unexpected recreation")
        assert not completed_render_generation_matches(**kwargs)
        deleted.unlink()
    (paths.infra_dir / "main.tf").write_text("drift")
    assert not completed_render_generation_matches(**kwargs)


def test_incomplete_publication_is_not_a_completed_recovery_checkpoint(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("old")
    paths = resolve_project_paths(config)
    assert ProjectBundleTransaction(paths.project_dir).completed_generation() is None

    def interrupt(name):
        if name == "after-commit":
            raise RuntimeError("interrupted")

    transaction = ProjectBundleTransaction(paths.project_dir, failpoint=interrupt)
    with pytest.raises(RuntimeError, match="interrupted"):
        transaction.commit({config: b"new"})
    before = transaction.journal_path.read_bytes()
    assert transaction.completed_generation() is None
    assert config.read_text() == "old"
    assert transaction.journal_path.read_bytes() == before


def test_fast_smoke_receipt_created_after_publication_is_not_a_render_tombstone(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("{}\n")
    paths = resolve_project_paths(config)
    staged = staged_generated_paths(paths)
    plan = build_project_generation_plan(
        final_paths=paths, staged_paths=staged, config_path=config, config_content="{}\n"
    )
    transaction = ProjectBundleTransaction(paths.project_dir)
    transaction.commit(
        plan.writes, expected_preimages=plan.expected_preimages, generation_sha256=plan.sha256
    )
    paths.reports_dir.mkdir(parents=True)
    receipt = paths.reports_dir / "soperator-fast-readiness-operation.json"
    receipt.write_text('{"status":"complete","job":"42"}\n')
    before = receipt.read_bytes()
    desired = build_project_generation_plan(
        final_paths=paths, staged_paths=staged, config_path=config, config_content="{}\n"
    )
    assert receipt not in desired.writes and receipt not in desired.removals
    assert completed_render_generation_matches(
        paths=paths,
        desired=desired,
        admitted_sha256=plan.sha256,
        admitted_preimage_sha256=plan.preimage_sha256,
    )
    assert receipt.read_bytes() == before
