"""Provenance and deterministic source selection, without network or builds."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.deploy import agent_artifact as artifacts
from nebius_vpngw.deploy.ssh_push import VMHAAgentArtifactError


@pytest.fixture(autouse=True)
def private_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("VPNGW_AGENT_WHEEL", raising=False)


def installed(monkeypatch, direct=None):
    monkeypatch.setattr(
        artifacts.metadata,
        "distribution",
        lambda _: SimpleNamespace(
            version="0.6.6",
            read_text=lambda _: None if direct is None else json.dumps(direct),
        ),
    )


def test_index_install_uses_exact_manifest_and_retains_digest_cache(monkeypatch):
    content = b"candidate"
    digest = hashlib.sha256(content).hexdigest()
    manifest = {
        "schema": artifacts.SCHEMA,
        "version": "0.6.6",
        "filename": "nebius_vpngw-0.6.6-py3-none-any.whl",
        "sha256": digest,
    }
    calls = []
    installed(monkeypatch)

    def download(url, **kwargs):
        calls.append(url)
        return json.dumps(manifest).encode() if url.endswith(".json") else content

    monkeypatch.setattr(artifacts, "_download", download)
    path, source = artifacts.resolve(None)
    assert path.parent.name == digest and path.read_bytes() == content
    assert source == "installed-provenance"
    again, source = artifacts.resolve(None)
    assert again == path and source == "installed-provenance-cache"
    assert len([url for url in calls if url.endswith(".whl")]) == 1


def test_direct_install_checks_hash_even_when_url_is_unchanged(monkeypatch):
    installed(
        monkeypatch,
        {
            "url": "https://example.org/nebius_vpngw-0.6.6-py3-none-any.whl",
            "archive_info": {"hashes": {"sha256": "a" * 64}},
        },
    )
    monkeypatch.setattr(artifacts, "_download", lambda *args, **kwargs: b"replacement")
    with pytest.raises(VMHAAgentArtifactError, match="recorded SHA-256"):
        artifacts.resolve(None)


def test_deleted_original_can_recover_only_identical_official_release(tmp_path, monkeypatch):
    wheel = tmp_path / "nebius_vpngw-0.6.6-py3-none-any.whl"
    installed(monkeypatch, {"url": wheel.as_uri(), "archive_info": {"hash": "sha256=" + "a" * 64}})
    monkeypatch.setattr(artifacts, "_manifest", lambda _: {"sha256": "b" * 64})
    with pytest.raises(VMHAAgentArtifactError, match="differs from the originally installed"):
        artifacts.resolve(None)


def test_source_build_failure_never_falls_back_to_old_release(tmp_path, monkeypatch):
    monkeypatch.setattr(
        artifacts, "build_source", lambda _: (_ for _ in ()).throw(RuntimeError("build failed"))
    )
    monkeypatch.setattr(
        artifacts.metadata, "distribution", lambda _: pytest.fail("old artifact fallback")
    )
    with pytest.raises(RuntimeError, match="build failed"):
        artifacts.resolve(tmp_path)


def test_override_is_explicit_and_empty_override_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("VPNGW_AGENT_WHEEL", str(tmp_path / "explicit.whl"))
    assert artifacts.resolve(None) == (tmp_path / "explicit.whl", "VPNGW_AGENT_WHEEL")
    monkeypatch.setenv("VPNGW_AGENT_WHEEL", "")
    with pytest.raises(VMHAAgentArtifactError, match="empty"):
        artifacts.resolve(None)


def test_cache_rejects_changed_bytes_and_symlink(tmp_path):
    wheel = tmp_path / "nebius_vpngw-0.6.6-py3-none-any.whl"
    wheel.write_bytes(b"original")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    cached = artifacts.cache_wheel(wheel, expected=digest)
    cached.write_bytes(b"changed")
    with pytest.raises(VMHAAgentArtifactError, match="conflicts"):
        artifacts.cache_wheel(wheel, expected=digest)
    wheel.unlink()
    wheel.symlink_to(cached)
    with pytest.raises(OSError):
        artifacts.cache_wheel(wheel)


def test_release_manifest_contains_only_public_artifact_identity(tmp_path):
    wheel = tmp_path / "nebius_vpngw-0.6.6-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    manifest = artifacts.release_manifest(wheel, "0.6.6")
    assert manifest == {
        "schema": artifacts.SCHEMA,
        "version": "0.6.6",
        "filename": wheel.name,
        "sha256": hashlib.sha256(b"wheel").hexdigest(),
    }


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b'{"schema":"foreign"}'])
def test_malformed_release_provenance_has_typed_artifact_failure(monkeypatch, raw):
    installed(monkeypatch)
    monkeypatch.setattr(artifacts, "_download", lambda *a, **kw: raw)
    with pytest.raises(VMHAAgentArtifactError):
        artifacts.resolve(None)
