"""Native interruption evidence must work even when service assets need no change."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

_DRIVER = Path(__file__).parents[1] / 'systemd/package_upgrade_driver.py'
_TREE = ast.parse(_DRIVER.read_text())
_SOURCE = next(
    ast.literal_eval(node.value)
    for node in _TREE.body
    if isinstance(node, ast.Assign)
    and any(getattr(target, 'id', '') == 'INTERRUPTION_ASSETS' for target in node.targets)
)
_NAMESPACE: dict = {}
exec(_SOURCE, _NAMESPACE)
observe = _NAMESPACE['interruption_assets']


def test_identical_predecessor_and_candidate_assets_preserve_real_interruption_evidence(tmp_path):
    asset = tmp_path / 'unit.service'
    asset.write_text('[Service]\nExecStart=/usr/bin/example\n')
    assets = {str(asset): {'sha256': hashlib.sha256(asset.read_bytes()).hexdigest()}}
    # The former "some bytes must differ" oracle rejects this valid predecessor.
    assert not any(
        not Path(path).exists()
        or hashlib.sha256(Path(path).read_bytes()).hexdigest() != item['sha256']
        for path, item in assets.items()
    )
    receipt = json.loads(json.dumps({'assets_before_product_install': observe(assets)}))
    assert observe(assets) == receipt['assets_before_product_install']
    assert receipt['assets_before_product_install'][str(asset)]['kind'] == 'file'


@pytest.mark.parametrize('change', ['bytes', 'mode', 'removed', 'created', 'replaced'])
def test_asset_publication_or_drift_cannot_pass_interrupted_before_publication_check(tmp_path, change):
    asset = tmp_path / 'unit.service'
    if change != 'created':
        asset.write_text('original')
    assets = {str(asset): {}}
    before = observe(assets)
    if change == 'bytes':
        asset.write_text('changed')
    elif change == 'mode':
        asset.chmod(0o700)
    elif change == 'removed':
        asset.unlink()
    elif change == 'created':
        asset.write_text('new')
    else:
        replacement = tmp_path / 'replacement'
        replacement.write_text('original')
        replacement.replace(asset)
    assert observe(assets) != before


@pytest.mark.parametrize('kind', ['symlink', 'directory', 'empty'])
def test_unsafe_or_empty_asset_inventory_is_rejected(tmp_path, kind):
    asset = tmp_path / 'unit.service'
    if kind == 'symlink':
        asset.symlink_to('missing')
    elif kind == 'directory':
        asset.mkdir()
    with pytest.raises(AssertionError):
        observe({} if kind == 'empty' else {str(asset): {}})


def test_both_native_fault_and_independent_observer_embed_the_same_inventory_code():
    for name in ('INTERRUPTION_HOOK', 'INTERRUPTION_OBSERVATION'):
        node = next(node for node in _TREE.body if isinstance(node, ast.Assign)
                    and any(getattr(target, 'id', '') == name for target in node.targets))
        assert isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.Add)
        assert isinstance(node.value.left, ast.Name) and node.value.left.id == 'INTERRUPTION_ASSETS'
        assert 'assets_before_product_install' in ast.literal_eval(node.value.right)
