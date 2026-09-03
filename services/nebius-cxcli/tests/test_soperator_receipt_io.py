from __future__ import annotations

import os
from pathlib import Path

import pytest

from nebius_cxcli.soperator_receipt_io import (
    read_owner_only_json,
    write_owner_only_json,
)


def test_owner_only_json_write_replaces_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    victim = tmp_path / "victim.json"
    victim.write_text("do not replace\n", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt.symlink_to(victim)

    write_owner_only_json(receipt, {"schema": "example/v1"})

    assert victim.read_text(encoding="utf-8") == "do not replace\n"
    assert not receipt.is_symlink()
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert read_owner_only_json(receipt, label="test receipt") == {
        "schema": "example/v1"
    }


def test_owner_only_json_write_does_not_use_predictable_pid_temporary_file(
    tmp_path: Path,
) -> None:
    victim = tmp_path / "victim.json"
    victim.write_text("do not replace\n", encoding="utf-8")
    legacy_temporary = tmp_path / f".receipt.json.{os.getpid()}.tmp"
    legacy_temporary.symlink_to(victim)

    write_owner_only_json(tmp_path / "receipt.json", {"accepted": True})

    assert victim.read_text(encoding="utf-8") == "do not replace\n"
    assert legacy_temporary.is_symlink()


def test_owner_only_json_io_rejects_symlink_parent(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(RuntimeError, match="directory is not safe"):
        write_owner_only_json(linked_parent / "receipt.json", {"accepted": True})

    assert not (real_parent / "receipt.json").exists()


def test_owner_only_json_io_rejects_symlink_ancestor(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(external, target_is_directory=True)

    with pytest.raises(RuntimeError, match="directory is not safe"):
        write_owner_only_json(
            linked / "generated" / "reports" / "receipt.json",
            {"accepted": True},
        )

    assert not (external / "generated").exists()


def test_owner_only_json_read_rejects_symlinks_hard_links_and_broad_modes(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "receipt.json"
    write_owner_only_json(receipt, {"accepted": True})

    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(receipt)
    with pytest.raises(RuntimeError, match="owner-only regular file"):
        read_owner_only_json(symlink, label="test receipt")

    hard_link = tmp_path / "hard-link.json"
    os.link(receipt, hard_link)
    with pytest.raises(RuntimeError, match="owner-only regular file"):
        read_owner_only_json(receipt, label="test receipt")
    hard_link.unlink()

    receipt.chmod(0o640)
    with pytest.raises(RuntimeError, match="owner-only regular file"):
        read_owner_only_json(receipt, label="test receipt")
