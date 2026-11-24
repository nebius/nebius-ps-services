import json

import pytest

from nebius_train.artifacts import atomic_json, tree_hash


def test_tree_identity_changes_with_input_bytes(tmp_path):
    item = tmp_path / "input"
    item.write_text("one")
    before = tree_hash(tmp_path)
    item.write_text("two")
    assert tree_hash(tmp_path) != before


def test_tree_rejects_symlink(tmp_path):
    (tmp_path / "escape").symlink_to("/etc/hosts")
    with pytest.raises(ValueError, match="symlink"):
        tree_hash(tmp_path)


def test_json_failure_preserves_previous_record(tmp_path):
    path = tmp_path / "record.json"
    atomic_json(path, {"ok": 1})
    with pytest.raises(ValueError):
        atomic_json(path, {"bad": float("nan")})
    assert json.loads(path.read_text()) == {"ok": 1}
    assert list(tmp_path.iterdir()) == [path]
