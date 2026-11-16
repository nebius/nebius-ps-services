from unittest.mock import Mock

import pytest

from nebius_vpngw.deploy import ordinary_apply
from nebius_vpngw.deploy.preparation_cache import PreparationCache
from nebius_vpngw.deploy.recovery import CURRENT, Invocation


def test_cache_reuses_immutable_files_and_rejects_tampering(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    wheel = first / "dependency.whl"
    wheel.write_bytes(b"verified package")
    prepare = Mock(return_value=[wheel])
    cache = PreparationCache()
    try:
        assert cache.files("inputs", first, prepare) == [wheel]
        wheel.unlink()
        assert cache.files("inputs", second, prepare)[0].read_bytes() == b"verified package"
        prepare.assert_called_once_with()
        retained = cache._bundles["inputs"][0][0]
        retained.chmod(0o600)
        retained.write_bytes(b"changed")
        with pytest.raises(RuntimeError, match="integrity"):
            cache.files("inputs", second, prepare)
    finally:
        cache.close()
    assert not retained.exists()


def test_dependency_reuse_requires_current_inventory_and_preserves_fresh_observation(
    monkeypatch, tmp_path
):
    context = Invocation("apply")
    token = CURRENT.set(context)
    resolved = Mock(return_value=[])
    monkeypatch.setattr(ordinary_apply, "_resolve_dependencies", resolved)
    manifest = {"requirements": ["managed>=1"]}
    observed = {
        "dependencies": {"managed": "1"},
        "requirements": {},
        "markers": {},
        "machine": "x86_64",
        "python": "3.12",
        "wheel_tags": ["tag"],
        "authority": "first",
    }
    try:
        ordinary_apply.resolve_dependencies(manifest, observed, tmp_path)
        ordinary_apply.resolve_dependencies(
            manifest, {**observed, "authority": "changed"}, tmp_path
        )
        assert resolved.call_count == 1
        ordinary_apply.resolve_dependencies(
            manifest, {**observed, "dependencies": {"managed": "2"}}, tmp_path
        )
        assert resolved.call_count == 2
    finally:
        CURRENT.reset(token)
        context.preparation.close()
