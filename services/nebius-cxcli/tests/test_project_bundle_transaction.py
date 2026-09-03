from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from nebius_cxcli.project_bundle_transaction import (
    ProjectBundleSafetyError,
    ProjectBundleTransaction,
)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "tenant" / "project"
    project.mkdir(parents=True)
    return project


def test_transaction_materializes_one_complete_generation(tmp_path: Path) -> None:
    project = _project(tmp_path)
    config = project / "config.yaml"
    manifest = project / "generated" / "manifest.json"
    config.write_text("old-config\n", encoding="utf-8")
    manifest.parent.mkdir()
    manifest.write_text("old-manifest\n", encoding="utf-8")

    ProjectBundleTransaction(project).commit({config: "new-config\n", manifest: "new-manifest\n"})

    assert config.read_text(encoding="utf-8") == "new-config\n"
    assert manifest.read_text(encoding="utf-8") == "new-manifest\n"
    journal = json.loads(
        (project / ".nebius-cxcli" / "project-bundle-transaction.json").read_text(encoding="utf-8")
    )
    assert journal["status"] == "complete"
    assert "new-config" not in json.dumps(journal)


def test_transaction_materializes_writes_and_deletions_as_one_generation(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    config = project / "config.yaml"
    retained = project / "generated" / "manifest.json"
    removed = project / "generated" / "stale.tf.json"
    config.write_text("old-config\n", encoding="utf-8")
    retained.parent.mkdir()
    retained.write_text("old-manifest\n", encoding="utf-8")
    removed.write_text("removed-file-content\n", encoding="utf-8")

    ProjectBundleTransaction(project).commit(
        {config: "new-config\n", retained: "new-manifest\n"},
        removals=(removed,),
    )

    assert config.read_text(encoding="utf-8") == "new-config\n"
    assert retained.read_text(encoding="utf-8") == "new-manifest\n"
    assert not removed.exists()
    journal = json.loads(
        (project / ".nebius-cxcli" / "project-bundle-transaction.json").read_text(encoding="utf-8")
    )
    tombstone = next(item for item in journal["targets"] if item["action"] == "delete")
    assert tombstone["newSha256"] == "absent"
    assert "removed-file-content" not in json.dumps(journal)


def test_crash_after_commit_recovers_forward(tmp_path: Path) -> None:
    project = _project(tmp_path)
    first = project / "config.yaml"
    second = project / "generated" / "manifest.json"
    first.write_text("old-a", encoding="utf-8")
    second.parent.mkdir()
    second.write_text("old-b", encoding="utf-8")

    def failpoint(name: str) -> None:
        if name == "after-commit":
            raise OSError("simulated crash")

    with pytest.raises(OSError, match="simulated crash"):
        ProjectBundleTransaction(project, failpoint=failpoint).commit(
            {first: b"new-a", second: b"new-b"}
        )

    assert first.read_bytes() == b"old-a"
    assert second.read_bytes() == b"old-b"
    assert ProjectBundleTransaction(project).recover()
    assert first.read_bytes() == b"new-a"
    assert second.read_bytes() == b"new-b"


def test_current_generation_recovers_a_committed_generation(tmp_path: Path) -> None:
    project = _project(tmp_path)
    target = project / "config.yaml"
    target.write_text("old", encoding="utf-8")
    generation_sha256 = "sha256:" + "a" * 64

    def failpoint(name: str) -> None:
        if name == "after-commit":
            raise OSError("simulated crash")

    with pytest.raises(OSError, match="simulated crash"):
        ProjectBundleTransaction(project, failpoint=failpoint).commit(
            {target: b"new"},
            generation_sha256=generation_sha256,
        )

    transaction = ProjectBundleTransaction(project)
    assert transaction.current_generation_sha256() == generation_sha256
    assert target.read_bytes() == b"new"


def test_crash_after_commit_recovers_pending_deletion_forward(tmp_path: Path) -> None:
    project = _project(tmp_path)
    config = project / "config.yaml"
    removed = project / "generated" / "stale.yaml"
    config.write_text("old", encoding="utf-8")
    removed.parent.mkdir()
    removed.write_text("stale", encoding="utf-8")

    def failpoint(name: str) -> None:
        if name == "after-commit":
            raise OSError("simulated crash")

    with pytest.raises(OSError, match="simulated crash"):
        ProjectBundleTransaction(project, failpoint=failpoint).commit(
            {config: b"new"},
            removals=(removed,),
        )

    assert config.read_bytes() == b"old"
    assert removed.read_bytes() == b"stale"
    assert ProjectBundleTransaction(project).recover()
    assert config.read_bytes() == b"new"
    assert not removed.exists()


def test_recovery_rejects_foreign_edit_to_deletion_target(tmp_path: Path) -> None:
    project = _project(tmp_path)
    config = project / "config.yaml"
    removed = project / "generated" / "stale.yaml"
    config.write_text("old", encoding="utf-8")
    removed.parent.mkdir()
    removed.write_text("stale", encoding="utf-8")

    def failpoint(name: str) -> None:
        if name == "after-commit":
            raise OSError("simulated crash")

    with pytest.raises(OSError):
        ProjectBundleTransaction(project, failpoint=failpoint).commit(
            {config: b"new"},
            removals=(removed,),
        )
    removed.write_text("operator-edit", encoding="utf-8")

    with pytest.raises(ProjectBundleSafetyError, match="changed outside"):
        ProjectBundleTransaction(project).recover()
    assert config.read_bytes() == b"old"
    assert removed.read_bytes() == b"operator-edit"


def test_transaction_rejects_absent_deletion_target(tmp_path: Path) -> None:
    project = _project(tmp_path)

    with pytest.raises(ProjectBundleSafetyError, match="delete an absent target"):
        ProjectBundleTransaction(project).commit({}, removals=(project / "missing",))


def test_crash_before_commit_leaves_old_generation_authoritative(tmp_path: Path) -> None:
    project = _project(tmp_path)
    target = project / "config.yaml"
    target.write_text("old", encoding="utf-8")

    def failpoint(name: str) -> None:
        if name == "before-commit":
            raise OSError("simulated crash")

    with pytest.raises(OSError, match="simulated crash"):
        ProjectBundleTransaction(project, failpoint=failpoint).commit({target: b"new"})

    assert target.read_bytes() == b"old"
    assert not ProjectBundleTransaction(project).recover()


def test_recovery_rejects_foreign_edit_before_materializing_any_target(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    first = project / "a"
    second = project / "b"
    first.write_text("old-a", encoding="utf-8")
    second.write_text("old-b", encoding="utf-8")

    def failpoint(name: str) -> None:
        if name == "after-commit":
            raise OSError("simulated crash")

    with pytest.raises(OSError):
        ProjectBundleTransaction(project, failpoint=failpoint).commit(
            {first: b"new-a", second: b"new-b"}
        )
    second.write_text("operator-edit", encoding="utf-8")

    with pytest.raises(ProjectBundleSafetyError, match="changed outside"):
        ProjectBundleTransaction(project).recover()
    assert first.read_bytes() == b"old-a"
    assert second.read_bytes() == b"operator-edit"


def test_transaction_rejects_symlink_target(tmp_path: Path) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    target = project / "config.yaml"
    target.symlink_to(outside)

    with pytest.raises(ProjectBundleSafetyError, match="regular file"):
        ProjectBundleTransaction(project).commit({target: b"replacement"})

    assert outside.read_text(encoding="utf-8") == "outside"


def test_transaction_accepts_an_alias_of_the_exact_project_root(tmp_path: Path) -> None:
    project = _project(tmp_path)
    target = project / "config.yaml"
    target.write_text("old", encoding="utf-8")
    alias = tmp_path / "project-alias"
    alias.symlink_to(project, target_is_directory=True)

    ProjectBundleTransaction(project).commit({alias / "config.yaml": b"replacement"})

    assert target.read_bytes() == b"replacement"


def test_transaction_still_rejects_a_symlink_below_the_project_root(tmp_path: Path) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    generated = project / "generated"
    generated.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectBundleSafetyError, match="unsafe parent"):
        ProjectBundleTransaction(project).commit({generated / "manifest.yaml": b"unsafe"})

    assert not (outside / "manifest.yaml").exists()


def test_transaction_rejects_hardlinked_target(tmp_path: Path) -> None:
    project = _project(tmp_path)
    target = project / "config.yaml"
    alias = project / "config-alias.yaml"
    target.write_text("old", encoding="utf-8")
    os.link(target, alias)

    with pytest.raises(ProjectBundleSafetyError, match="uniquely owner-controlled"):
        ProjectBundleTransaction(project).commit({target: b"replacement"})

    assert target.read_text(encoding="utf-8") == "old"
    assert alias.read_text(encoding="utf-8") == "old"


def test_transaction_preserves_owner_read_only_target_mode(tmp_path: Path) -> None:
    project = _project(tmp_path)
    target = project / "config.yaml"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o400)

    ProjectBundleTransaction(project).commit({target: b"new"})

    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o400


def test_completed_generation_does_not_fence_later_operator_edit(tmp_path: Path) -> None:
    project = _project(tmp_path)
    target = project / "config.yaml"
    target.write_text("old", encoding="utf-8")
    transaction = ProjectBundleTransaction(project)

    transaction.commit(
        {target: b"generated"},
        generation_sha256="sha256:" + "a" * 64,
    )
    target.write_text("operator-edit", encoding="utf-8")

    assert transaction.recover()
    assert target.read_text(encoding="utf-8") == "operator-edit"
    assert transaction.current_generation_sha256() is None


def test_completed_generation_allows_a_consecutive_generation(tmp_path: Path) -> None:
    project = _project(tmp_path)
    target = project / "config.yaml"
    target.write_text("old", encoding="utf-8")
    transaction = ProjectBundleTransaction(project)

    generation_a = "sha256:" + "a" * 64
    generation_b = "sha256:" + "b" * 64
    transaction.commit({target: b"generation-a"}, generation_sha256=generation_a)
    journal = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
    staged = transaction.generations_dir / journal["generationId"] / "config.yaml"
    staged.unlink()
    assert transaction.recover()
    assert transaction.current_generation_sha256() == generation_a
    transaction.commit({target: b"generation-b"}, generation_sha256=generation_b)

    assert target.read_bytes() == b"generation-b"
    assert transaction.current_generation_sha256() == generation_b


def test_current_generation_without_a_journal_is_absent(tmp_path: Path) -> None:
    assert ProjectBundleTransaction(_project(tmp_path)).current_generation_sha256() is None


def test_snapshot_preimages_returns_matching_bytes_digests_and_absence(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    existing = project / "config.yaml"
    absent = project / "generated" / "manifest.json"
    existing.write_bytes(b"canonical-config\n")

    preimages = ProjectBundleTransaction(project).snapshot_preimages((absent, existing))

    assert list(preimages) == [existing, absent]
    assert preimages[existing].path == existing
    assert preimages[existing].content == b"canonical-config\n"
    assert preimages[existing].sha256 == (
        "sha256:" + hashlib.sha256(b"canonical-config\n").hexdigest()
    )
    assert preimages[absent].path == absent
    assert preimages[absent].content is None
    assert preimages[absent].sha256 == "absent"


def test_snapshot_preimages_recovers_committed_generation_first(tmp_path: Path) -> None:
    project = _project(tmp_path)
    target = project / "config.yaml"
    target.write_bytes(b"old")

    def failpoint(name: str) -> None:
        if name == "after-commit":
            raise OSError("simulated crash")

    with pytest.raises(OSError, match="simulated crash"):
        ProjectBundleTransaction(project, failpoint=failpoint).commit({target: b"new"})

    preimage = ProjectBundleTransaction(project).snapshot_preimages((target,))[target]

    assert preimage.content == b"new"
    assert target.read_bytes() == b"new"


def test_snapshot_preimages_rejects_duplicate_target(tmp_path: Path) -> None:
    project = _project(tmp_path)
    target = project / "config.yaml"

    with pytest.raises(ProjectBundleSafetyError, match="repeats target"):
        ProjectBundleTransaction(project).snapshot_preimages((target, target))


def test_expected_preimages_reject_concurrent_edit_without_partial_update(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    first = project / "config.yaml"
    second = project / "generated" / "manifest.json"
    first.write_bytes(b"old-config")
    second.parent.mkdir()
    second.write_bytes(b"old-manifest")
    transaction = ProjectBundleTransaction(project)
    preimages = transaction.snapshot_preimages((first, second))
    second.write_bytes(b"operator-edit")

    with pytest.raises(ProjectBundleSafetyError, match="changed after generation admission"):
        transaction.commit(
            {first: b"new-config", second: b"new-manifest"},
            expected_preimages={path: preimage.sha256 for path, preimage in preimages.items()},
        )

    assert first.read_bytes() == b"old-config"
    assert second.read_bytes() == b"operator-edit"


@pytest.mark.parametrize(
    ("invalid_case", "expected_error"),
    [
        ("generation-id", "generation identity"),
        ("generation-digest", "logical generation digest"),
        ("empty-targets", "has no targets"),
        ("duplicate-target", "repeats a target"),
        ("invalid-action", "target action is invalid"),
        ("invalid-old-digest", "preimage digest is invalid"),
        ("invalid-new-digest", "postimage digest is invalid"),
    ],
)
def test_current_generation_rejects_invalid_historical_metadata(
    tmp_path: Path,
    invalid_case: str,
    expected_error: str,
) -> None:
    project = _project(tmp_path)
    target = project / "config.yaml"
    target.write_text("old", encoding="utf-8")
    transaction = ProjectBundleTransaction(project)
    transaction.commit({target: b"new"})
    journal = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
    if invalid_case == "generation-id":
        journal["generationId"] = "../invalid"
    elif invalid_case == "generation-digest":
        journal["generationSha256"] = "invalid"
    elif invalid_case == "empty-targets":
        journal["targets"] = []
    elif invalid_case == "duplicate-target":
        journal["targets"].append(dict(journal["targets"][0]))
    elif invalid_case == "invalid-action":
        journal["targets"][0]["action"] = "invalid"
    elif invalid_case == "invalid-old-digest":
        journal["targets"][0]["oldSha256"] = "sha256:not-a-digest"
    else:
        journal["targets"][0]["newSha256"] = "sha256:not-a-digest"
    transaction.journal_path.write_text(json.dumps(journal), encoding="utf-8")
    transaction.journal_path.chmod(0o600)

    with pytest.raises(ProjectBundleSafetyError, match=expected_error):
        transaction.current_generation_sha256()


@pytest.mark.parametrize(
    ("field", "invalid_value", "expected_error"),
    [
        ("generationId", "..", "generation identity"),
        ("generationSha256", "sha256:not-a-digest", "logical generation digest"),
    ],
)
def test_current_generation_rejects_noncanonical_generation_metadata(
    tmp_path: Path,
    field: str,
    invalid_value: str,
    expected_error: str,
) -> None:
    project = _project(tmp_path)
    target = project / "config.yaml"
    target.write_text("old", encoding="utf-8")
    transaction = ProjectBundleTransaction(project)
    transaction.commit({target: b"new"})
    journal = json.loads(transaction.journal_path.read_text(encoding="utf-8"))
    journal[field] = invalid_value
    transaction.journal_path.write_text(json.dumps(journal), encoding="utf-8")
    transaction.journal_path.chmod(0o600)

    with pytest.raises(ProjectBundleSafetyError, match=expected_error):
        transaction.current_generation_sha256()
