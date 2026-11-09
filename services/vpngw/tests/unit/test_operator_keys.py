import stat

import pytest
from cryptography.hazmat.primitives import serialization

from nebius_vpngw.deploy import operator_keys as keys


def test_generated_pair_is_encrypted_private_and_retry_reuses_identity(tmp_path):
    path = tmp_path / "operators" / "alice" / "id_ed25519"
    first = keys.generate_or_resume(path, "test-only passphrase")
    original = path.read_bytes()
    again = keys.generate_or_resume(path, "test-only passphrase")
    assert first.fingerprint == again.fingerprint
    assert path.read_bytes() == original
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert first.private_path == path.absolute()
    assert first.public_path == path.with_name("id_ed25519.pub").absolute()
    assert first.pkey.get_base64() == first.public_key.split()[1]
    with pytest.raises(ValueError, match="original passphrase"):
        keys.generate_or_resume(path, "incorrect")
    assert path.read_bytes() == original


def test_crash_before_public_file_publication_resumes_same_pair(tmp_path, monkeypatch):
    path = tmp_path / "keys" / "id_ed25519"
    publish = keys.publish_new

    def fail_public(destination, content):
        if destination.suffix == ".pub":
            raise OSError("interrupted")
        publish(destination, content)

    monkeypatch.setattr(keys, "publish_new", fail_public)
    with pytest.raises(OSError):
        keys.generate_or_resume(path, "test-only passphrase")
    original = path.read_bytes()
    monkeypatch.setattr(keys, "publish_new", publish)
    result = keys.generate_or_resume(path, "test-only passphrase")
    assert result.public_path.exists() and path.read_bytes() == original


def test_public_only_import_cannot_claim_private_key_verification(tmp_path):
    result = keys.generate_or_resume(tmp_path / "keys" / "id_ed25519", "test-only passphrase")
    imported = keys.import_public(result.public_path)
    assert imported.fingerprint == result.fingerprint
    assert imported.pkey is None and imported.private_path is None
    with pytest.raises(ValueError, match="plain"):
        keys.public_identity('command="bad" ' + result.public_key)


def test_never_overwrite_existing_public_key_or_follow_symlink(tmp_path):
    parent = tmp_path / "keys"
    parent.mkdir(mode=0o700)
    path = parent / "id_ed25519"
    public = path.with_name(path.name + ".pub")
    public.write_text("existing public content")
    public.chmod(0o600)
    with pytest.raises(ValueError, match="refusing regeneration"):
        keys.generate_or_resume(path, "test-only passphrase")
    assert not path.exists()
    public.unlink()
    path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError):
        keys.generate_or_resume(path, "test-only passphrase")
    assert not (tmp_path / "elsewhere").exists()


def test_private_key_is_complete_before_canonical_name_publication(tmp_path, monkeypatch):
    path = tmp_path / "keys" / "id_ed25519"
    link = keys.os.link
    seen = []

    def inspect(source, destination, **kwargs):
        if destination == path:
            assert not path.exists()
            assert serialization.load_ssh_private_key(
                source.read_bytes(), password=b"test passphrase"
            )
            seen.append(True)
        return link(source, destination, **kwargs)

    monkeypatch.setattr(keys.os, "link", inspect)
    keys.generate_or_resume(path, "test passphrase")
    assert seen == [True]


def test_private_leaf_under_writable_nonsticky_ancestor_is_rejected(tmp_path):
    parent = tmp_path / "untrusted"
    parent.mkdir(mode=0o777)
    parent.chmod(0o777)
    leaf = parent / "keys"
    leaf.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="unsafe writable ancestor"):
        keys.generate_or_resume(leaf / "id_ed25519", "test passphrase")


@pytest.mark.parametrize("selector", ["public", "private"])
def test_key_selectors_reject_foreign_writable_ancestors(tmp_path, selector):
    from nebius_vpngw.deploy.ssh_client_auth import select_operator_identity

    pair = keys.generate_or_resume(tmp_path / "keys" / "id_ed25519", "test-only passphrase")
    pair.private_path.parent.chmod(0o777)
    with pytest.raises(ValueError, match="unsafe ancestor"):
        select_operator_identity(pair.public_path if selector == "public" else pair.private_path)


def test_public_import_rejects_change_during_read(tmp_path, monkeypatch):
    pair = keys.generate_or_resume(tmp_path / "keys" / "id_ed25519", "test-only passphrase")
    actual = keys.os.fstat
    calls = [0]

    def changed(fd):
        value = actual(fd)
        calls[0] += 1
        if calls[0] == 1:
            pair.public_path.write_text(pair.public_key + " changed-comment\n")
        return value

    monkeypatch.setattr(keys.os, "fstat", changed)
    with pytest.raises(ValueError, match="changed while"):
        keys.import_public(pair.public_path)
